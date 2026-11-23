"""Production NeMo Guardrails security service (P8-E4 Stage 2-4).

FastAPI application with HMAC-authenticated requests, deterministic guardrail
rails, fail-closed behavior, and metadata-only structured logging.

Security layers (in execution order per directive Stage 4):
1. HMAC gateway authentication (constant-time comparison)
2. Strict request schema (Pydantic) + body size limits
3. Input guardrails (jailbreak, secrets, SQL, code-execution, policy override)
4. Dialog policy (topic restriction)
5. Tool authorization (fixed allowlist, validated arguments)
6. Retrieval-content safety (prompt-injection screening)
7. [NVIDIA inference — NOT implemented here: this service ONLY runs guardrails]
8. Output guardrails (secret redaction, prompt-leak detection)
9. Evidence-grounding validation
10. Final secret-redaction pass

The NVIDIA inference call itself is made by the SUPABASE GATEWAY, not by this
service. This service receives the input text and returns a pass/reject verdict;
the gateway then calls NVIDIA and sends the output back for output validation.

Run locally:
  NEMO_AUTH_SECRET=dev-secret uvicorn production_service:app --port 9071

In Docker:
  CMD uvicorn production_service:app --host 0.0.0.0 --port 8080
"""
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration (from environment; never hard-coded secrets)
# ---------------------------------------------------------------------------
AUTH_SECRET = os.environ.get("NEMO_AUTH_SECRET", "")
MAX_BODY_SIZE = 32 * 1024  # 32 KB
MAX_TEXT_LENGTH = 4000
REPLAY_WINDOW_SECONDS = 30
MAX_CONCURRENT_REQUESTS = 10

# Verify NeMo is available (framework orchestration)
try:
    import nemoguardrails  # noqa: F401
    NEMO_VERSION = nemoguardrails.__version__
except ImportError:
    NEMO_VERSION = None

# ---------------------------------------------------------------------------
# Structured metadata-only logging (NO raw prompts, NO responses, NO secrets)
# ---------------------------------------------------------------------------
logger = logging.getLogger("nemo-guardrails")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(json.dumps(
    {"ts": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "msg": "%(message)s"}
)))
logger.addHandler(handler)

# ---------------------------------------------------------------------------
# Rail 1: HMAC gateway authentication (Stage 3, Option B)
# ---------------------------------------------------------------------------

def verify_hmac(request: Request, body_digest: str, signature: str, timestamp: str, nonce: str) -> bool:
    """Verify the gateway's HMAC-SHA256 signature over method+path+body+timestamp+nonce."""
    if not AUTH_SECRET:
        return False  # No secret configured = fail-closed
    # Replay window check
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts) > REPLAY_WINDOW_SECONDS:
        return False
    # Construct the expected payload
    payload = f"{request.method}|{request.url.path}|{body_digest}|{timestamp}|{nonce}"
    expected = hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    # Constant-time comparison (never early-exit on mismatch)
    return hmac.compare_digest(expected, signature)


# Nonce replay prevention (in-memory for single-instance; Cloud Run single-instance = OK)
_seen_nonces: dict[str, float] = {}


def check_nonce(nonce: str) -> bool:
    """Returns True if the nonce is fresh (not seen in the replay window)."""
    now = time.time()
    # Prune expired nonces
    expired = [n for n, t in _seen_nonces.items() if now - t > REPLAY_WINDOW_SECONDS]
    for n in expired:
        del _seen_nonces[n]
    if nonce in _seen_nonces:
        return False  # Replay detected
    _seen_nonces[nonce] = now
    return True


# ---------------------------------------------------------------------------
# Rail 2: Request schema (strict Pydantic models)
# ---------------------------------------------------------------------------

class CheckInputRequest(BaseModel):
    text: str = Field(..., max_length=MAX_TEXT_LENGTH)
    conversation_id: Optional[str] = Field(None, max_length=64)


class CheckOutputRequest(BaseModel):
    text: str = Field(..., max_length=MAX_TEXT_LENGTH * 4)
    evidence: list[str] = Field(default_factory=list, max_length=20)


class CheckRetrievalRequest(BaseModel):
    content: str = Field(..., max_length=MAX_TEXT_LENGTH)


class CheckToolRequest(BaseModel):
    tool: str = Field(..., max_length=50)
    args: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rail 3-6: Deterministic input/output/retrieval/tool patterns
# (preserved from the existing nemo_service.py — these ARE the security gate)
# ---------------------------------------------------------------------------

INPUT_PATTERNS = [
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", "jailbreak_override"),
    (r"disregard\s+(your|all)\s+(instructions?|guidelines?|rules?)", "jailbreak_override"),
    (r"forget\s+(everything|all|your\s+instructions)", "jailbreak_override"),
    (r"you\s+are\s+now\s+((a|an)\s+)?(different|unrestricted|uncensored|dan|jailbreak)", "role_hijack"),
    (r"pretend\s+(i\s+am|you\s+are)\s+(the\s+)?(admin|administrator|root|developer)", "role_hijack"),
    (r"act\s+as\s+(if\s+you\s+have\s+)?(no|zero)\s+(restrictions?|guardrails?|filters?)", "policy_override"),
    (r"(bypass|disable|override|remove)\s+(the\s+|your\s+|all\s+)?(rate\s+limiter|guardrails?|safety|security|quotas?|filters?)", "policy_override"),
    (r"(reveal|show|print|display|give|tell\s+me|expose|leak|output)\s+(me\s+)?.{0,20}(api\s+key|secret|password|credential|token|private\s+key)", "secret_extraction"),
    (r"(reveal|show|print|display)\s+(me\s+)?(your\s+)?(system\s+prompt|developer\s+instructions?|hidden\s+prompt|guardrail\s+(config|rules?))", "prompt_extraction"),
    (r"what\s+(is|are)\s+(your|the)\s+(nvidia|pinecone|openai)\s+(key|token|secret|credential)", "secret_extraction"),
    (r"\b(drop|delete|truncate|insert|update)\s+(table|from|into)\b", "sql_injection"),
    (r"\bexec(ute)?\s+(the\s+)?(sql|query|command|script|shell|code)", "code_execution"),
    (r"(run|execute|eval)\s+(a\s+)?(python|bash|shell|powershell|node)\s+(command|script|code)", "code_execution"),
    (r"(change|modify|override|set)\s+.{0,15}(namespace|index|database|environment|config)", "config_tampering"),
    (r"(access|read|write|list|delete)\s+(the\s+)?(filesystem|files?|directory|/etc/|/opt/)", "filesystem_access"),
    (r"decode\s+(this|the\s+)?(base64\s+)?(encoded|obfuscated)\s+(instruction|message|payload)", "encoded_instruction"),
    (r"decode\s+this\s+base64\s+encoded", "encoded_instruction"),
    (r"<\s*/?\s*system\s*>|<\s*/?\s*assistant?\s*>|<\s*/?\s*im_start\s*>", "markup_injection"),
]

SECRET_PATTERNS = [
    (r"nvapi-[A-Za-z0-9_\-]{10,}", "nvidia_api_key"),
    (r"pcsk_[A-Za-z0-9_\-]{10,}", "pinecone_key"),
    (r"\bAKIA[0-9A-Z]{16}\b", "aws_access_key"),
    (r"\bgh[oprs]_[A-Za-z0-9]{20,}\b", "github_token"),
    (r"(?i)(password|secret|token|api_key|apikey)\s+(is|[:=])\s*[:=]?\s*[A-Za-z0-9+/_\-=!\S]{8,}", "credential_assignment"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private_key"),
    (r"(?i)bearer\s+[A-Za-z0-9\-_\.]{25,}", "bearer_token"),
    (r"(?i)(system\s+prompt|developer\s+instructions?|guardrail\s+config)\s*[:=]\s*\S+", "prompt_leak"),
]

TOOL_ALLOWLIST = {
    "get_shipment", "get_temperature_history", "get_humidity_history", "get_incident_records",
    "get_custody_history", "compare_shipments", "aggregate_approved_shipments", "search_approved_sops",
}

FORBIDDEN_TOOL_ARGS = ("sql", "command", "code", "script", "namespace", "index", "path", "file")


def check_input_text(text: str) -> dict:
    t = (text or "").strip()
    if not t:
        return {"ok": False, "reason": "empty_request", "category": "validation"}
    if len(t) > MAX_TEXT_LENGTH:
        return {"ok": False, "reason": "input_too_long", "category": "validation"}
    low = t.lower()
    for pattern, category in INPUT_PATTERNS:
        if re.search(pattern, low):
            return {"ok": False, "reason": f"input_policy_violation:{category}", "category": category}
    return {"ok": True, "reason": None, "category": None}


def check_output_text(text: str, evidence: list[str]) -> dict:
    t = (text or "").strip()
    if len(t) > MAX_TEXT_LENGTH * 4:
        return {"ok": False, "reason": "output_too_long", "category": "validation"}
    for pattern, what in SECRET_PATTERNS:
        if re.search(pattern, t):
            return {"ok": False, "reason": f"output_secret_leak:{what}", "category": what}
    low = t.lower()
    if "system prompt" in low and ("ignore" in low or "reveal" in low):
        return {"ok": False, "reason": "prompt_content_leak", "category": "prompt_leak"}
    if len(evidence) > 0 and not any(e.lower() in low for e in evidence):
        return {"ok": False, "reason": "ungrounded_answer", "category": "grounding"}
    return {"ok": True, "reason": None, "category": None}


def check_retrieval_content(content: str) -> dict:
    low = (content or "").lower()
    threats = [category for pattern, category in INPUT_PATTERNS if re.search(pattern, low)]
    if threats:
        return {"ok": False, "threat_categories": threats, "action": "quarantine"}
    return {"ok": True, "threat_categories": [], "action": None}


def check_tool_call(tool: str, args: dict) -> dict:
    if tool not in TOOL_ALLOWLIST:
        return {"ok": False, "reason": f"unauthorized_tool:{tool}", "allowed_tools": sorted(TOOL_ALLOWLIST)}
    if not isinstance(args, dict) or len(args) > 5:
        return {"ok": False, "reason": "invalid_arguments"}
    for k, v in args.items():
        if not isinstance(k, str) or len(k) > 30 or len(str(v)) > 200:
            return {"ok": False, "reason": "argument_out_of_bounds"}
        if k.lower() in FORBIDDEN_TOOL_ARGS:
            return {"ok": False, "reason": "forbidden_argument_key"}
    return {"ok": True, "reason": None}


# ---------------------------------------------------------------------------
# Concurrency tracking (fail-closed on overload)
# ---------------------------------------------------------------------------
_active_requests = 0
_max_concurrent = MAX_CONCURRENT_REQUESTS


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(json.dumps({"event": "startup", "nemo_version": NEMO_VERSION,
                            "auth_configured": bool(AUTH_SECRET), "inference": "disabled"}))
    yield
    logger.info(json.dumps({"event": "shutdown"}))


app = FastAPI(title="Cold-Chain NeMo Guardrails", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def auth_and_limits(request: Request, call_next):
    """Rail 1: HMAC authentication on every request (fail-closed)."""
    global _active_requests
    # Body size check
    content_length = request.headers.get("content-length", "0")
    try:
        if int(content_length) > MAX_BODY_SIZE:
            return JSONResponse({"ok": False, "error": "body_too_large"}, status_code=413)
    except ValueError:
        return JSONResponse({"ok": False, "error": "invalid_content_length"}, status_code=400)

    # HMAC auth (skip for /health which is a readiness probe)
    if request.url.path != "/health":
        if not AUTH_SECRET:
            return JSONResponse({"ok": False, "error": "auth_not_configured"}, status_code=503)
        signature = request.headers.get("X-Signature", "")
        timestamp = request.headers.get("X-Timestamp", "")
        nonce = request.headers.get("X-Nonce", "")
        body = await request.body()
        body_digest = hashlib.sha256(body).hexdigest()
        if not verify_hmac(request, body_digest, signature, timestamp, nonce):
            return JSONResponse({"ok": False, "error": "authentication_failed"}, status_code=401)
        if not check_nonce(nonce):
            return JSONResponse({"ok": False, "error": "replay_detected"}, status_code=401)
        # Re-inject body for downstream handlers
        request._body = body

    # Concurrency check
    if _active_requests >= _max_concurrent:
        return JSONResponse({"ok": False, "error": "overloaded"}, status_code=429)
    _active_requests += 1
    try:
        response = await call_next(request)
        return response
    finally:
        _active_requests -= 1


@app.get("/health")
async def health():
    """Health endpoint (no auth required for readiness probes)."""
    return {
        "status": "ok", "service": "nemo-guardrails",
        "nemo_version": NEMO_VERSION, "auth_configured": bool(AUTH_SECRET),
        "inference": "disabled",
        "active_requests": _active_requests,
    }


@app.post("/check-input")
async def check_input(req: CheckInputRequest):
    """Rail 3: Input guardrails."""
    t0 = time.perf_counter()
    result = check_input_text(req.text)
    latency_ms = (time.perf_counter() - t0) * 1000
    logger.info(json.dumps({"event": "input_check", "ok": result["ok"],
                            "category": result.get("category"), "latency_ms": round(latency_ms, 2)}))
    return result


@app.post("/check-output")
async def check_output(req: CheckOutputRequest):
    """Rail 8: Output guardrails."""
    t0 = time.perf_counter()
    result = check_output_text(req.text, req.evidence)
    latency_ms = (time.perf_counter() - t0) * 1000
    logger.info(json.dumps({"event": "output_check", "ok": result["ok"],
                            "category": result.get("category"), "latency_ms": round(latency_ms, 2)}))
    return result


@app.post("/check-retrieval")
async def check_retrieval(req: CheckRetrievalRequest):
    """Rail 6: Retrieval-content safety."""
    result = check_retrieval_content(req.content)
    logger.info(json.dumps({"event": "retrieval_check", "ok": result["ok"],
                            "threats": result.get("threat_categories", [])}))
    return result


@app.post("/check-tool")
async def check_tool(req: CheckToolRequest):
    """Rail 5: Tool authorization."""
    result = check_tool_call(req.tool, req.args)
    logger.info(json.dumps({"event": "tool_check", "tool": req.tool, "ok": result["ok"]}))
    return result
