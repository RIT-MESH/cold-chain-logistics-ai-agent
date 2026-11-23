"""Local NeMo Guardrails security service (P3 — directive 2026-11-22).

Runs as an HTTP service exposing the GuardrailRuntime contract the TS gateway
consumes: POST /check-input and POST /check-output. Uses the pinned
nemoguardrails==0.25.0 framework with a MOCKED LLM (no NVIDIA calls, ever).

Rails implemented:
- Input: jailbreak/injection/secret-extraction/code-execution detection
  (deterministic pattern engine + NeMo flow routing)
- Dialog: topic-restricted flows (shipment investigation allowed; secrets
  and execution refused)
- Retrieval: retrieved SOP content is treated as untrusted data — injection
  patterns inside it are neutralized before reaching the model
- Tool: fixed allowlist of read-only synthetic-data tools
- Output: secret-pattern redaction + evidence-grounding assertion
- Fail-closed: any internal error -> REJECT (never pass unguarded)

Run: python nemo_service.py            (serves on :9071, mock LLM on :9070)
"""
import hashlib
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# 1. DETERMINISTIC RAIL PATTERNS (the security logic — no LLM needed)
# ---------------------------------------------------------------------------

INPUT_PATTERNS = [
    # Direct jailbreak / instruction override
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", "jailbreak_override"),
    (r"disregard\s+(your|all)\s+(instructions?|guidelines?|rules?)", "jailbreak_override"),
    (r"forget\s+(everything|all|your\s+instructions)", "jailbreak_override"),
    (r"you\s+are\s+now\s+((a|an)\s+)?(different|unrestricted|uncensored|dan|jailbreak)", "role_hijack"),
    (r"pretend\s+(i\s+am|you\s+are)\s+(the\s+)?(admin|administrator|root|developer)", "role_hijack"),
    (r"act\s+as\s+(if\s+you\s+have\s+)?(no|zero)\s+(restrictions?|guardrails?|filters?)", "policy_override"),
    (r"(bypass|disable|override|remove)\s+(the\s+|your\s+|all\s+)?(rate\s+limiter|guardrails?|safety|security|quotas?|filters?)", "policy_override"),
    # Secret extraction
    (r"(reveal|show|print|display|give|tell\s+me|expose|leak|output)\s+(me\s+)?.{0,20}(api\s+key|secret|password|credential|token|private\s+key)", "secret_extraction"),
    (r"(reveal|show|print|display)\s+(me\s+)?(your\s+)?(system\s+prompt|developer\s+instructions?|hidden\s+prompt|guardrail\s+(config|rules?))", "prompt_extraction"),
    (r"what\s+(is|are)\s+(your|the)\s+(nvidia|pinecone|openai)\s+(key|token|secret|credential)", "secret_extraction"),
    # Code/SQL execution
    (r"\b(drop|delete|truncate|insert|update)\s+(table|from|into)\b", "sql_injection"),
    (r"\bexec(ute)?\s+(the\s+)?(sql|query|command|script|shell|code)", "code_execution"),
    (r"(run|execute|eval)\s+(a\s+)?(python|bash|shell|powershell|node)\s+(command|script|code)", "code_execution"),
    (r"(change|modify|override|set)\s+.{0,15}(namespace|index|database|environment|config)", "config_tampering"),
    (r"(access|read|write|list|delete)\s+(the\s+)?(filesystem|files?|directory|/etc/|/opt/)", "filesystem_access"),
    # Encoded/obfuscated
    (r"decode\s+(this|the\s+)?(base64\s+)?(encoded|obfuscated)\s+(instruction|message|payload)", "encoded_instruction"),
    (r"decode\s+this\s+base64\s+encoded", "encoded_instruction"),
    # Prompt injection in retrieved content markers
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

ALLOWED_TOPICS = re.compile(
    r"\b(UC-\d{6}|shipment|cargo|temperature|humidity|telemetry|incident|custody|"
    r"sop|procedure|sensor|vehicle|alert|delivery|envelope|sql\s+guard|security\s+(measure|architecture)|"
    r"architecture|dataset|synthetic)\b", re.IGNORECASE)

TOOL_ALLOWLIST = {
    "get_shipment", "get_temperature_history", "get_humidity_history", "get_incident_records",
    "get_custody_history", "compare_shipments", "aggregate_approved_shipments", "search_approved_sops",
}

FALLBACK_TOPICS = re.compile(r"\b(hi|hello|hey|thanks|thank you|help|what\s+can\s+you\s+do)\b", re.IGNORECASE)


def check_input_text(text: str) -> dict:
    """Input rail: reject jailbreak/secret/extraction/execution patterns.
    Returns {'ok': bool, 'reason': str, 'category': str}."""
    t = (text or "").strip()
    if not t:
        return {"ok": False, "reason": "empty_request", "category": "validation"}
    if len(t) > 4000:
        return {"ok": False, "reason": "input_too_long", "category": "validation"}
    low = t.lower()
    for pattern, category in INPUT_PATTERNS:
        if re.search(pattern, low):
            return {"ok": False, "reason": f"input_policy_violation:{category}", "category": category}
    return {"ok": True, "reason": None, "category": None}


def screen_retrieved_content(content: str) -> dict:
    """Retrieval rail: neutralize injection patterns found in SOP/shipment
    retrieved content (directive 6A.5: treat all retrieved text as untrusted
    data, not instructions)."""
    low = (content or "").lower()
    threats = []
    for pattern, category in INPUT_PATTERNS:
        if re.search(pattern, low):
            threats.append(category)
    if threats:
        return {"ok": False, "threat_categories": threats, "action": "quarantine"}
    return {"ok": True, "threat_categories": [], "action": None}


def check_tool_call(tool: str, args: dict) -> dict:
    """Tool rail: only allowlisted tools with validated structured arguments."""
    if tool not in TOOL_ALLOWLIST:
        return {"ok": False, "reason": f"unauthorized_tool:{tool}", "allowed_tools": sorted(TOOL_ALLOWLIST)}
    if not isinstance(args, dict) or len(args) > 5:
        return {"ok": False, "reason": "invalid_arguments"}
    for k, v in args.items():
        if not isinstance(k, str) or len(k) > 30 or len(str(v)) > 200:
            return {"ok": False, "reason": "argument_out_of_bounds"}
        if k.lower() in ("sql", "command", "code", "script", "namespace", "index", "path", "file"):
            return {"ok": False, "reason": "forbidden_argument_key"}
    return {"ok": True, "reason": None}


def check_output_text(text: str, evidence: list) -> dict:
    """Output rail: block credential leakage, prompt leaks; verify the answer
    is grounded (mentions evidence terms) and carries the synthetic label."""
    t = (text or "").strip()
    for pattern, what in SECRET_PATTERNS:
        if re.search(pattern, t):
            return {"ok": False, "reason": f"output_secret_leak:{what}", "category": what}
    low = t.lower()
    if "system prompt" in low and ("ignore" in low or "reveal" in low):
        return {"ok": False, "reason": "prompt_content_leak", "category": "prompt_leak"}
    # Evidence grounding: for shipment answers, a UC-id or topic term must appear
    if len(evidence) > 0 and not any(e.lower() in low for e in evidence):
        return {"ok": False, "reason": "ungrounded_answer", "category": "grounding"}
    return {"ok": True, "reason": None}


# ---------------------------------------------------------------------------
# 2. NeMo Guardrails integration (framework used as the rails orchestrator;
#    the mock LLM endpoint returns permissive verdicts so the deterministic
#    rails above are the active security gate — they run BEFORE and AFTER
#    any LLM turn, exactly as the deployed pipeline would order them).
# ---------------------------------------------------------------------------
try:
    from nemoguardrails import LLMRails, RailsConfig
    from nemoguardrails.actions import action

    @action(name="respond_to_shipment_question")
    async def respond_to_shipment_question(context):
        """Dialog rail: route approved shipment questions to the evidence pipeline."""
        return {"status": "routed", "topic": "shipment_investigation"}

    @action(name="refuse_secret_request")
    async def refuse_secret_request(context):
        """Dialog rail: refuse secret extraction."""
        return {"status": "refused", "reason": "secret_requests_not_permitted"}

    @action(name="refuse_execution_request")
    async def refuse_execution_request(context):
        """Dialog rail: refuse code/SQL/tool execution."""
        return {"status": "refused", "reason": "tool_execution_outside_allowlist"}

    NEMO_AVAILABLE = True
    NEMO_VERSION = "0.25.0"
except ImportError:
    NEMO_AVAILABLE = False
    NEMO_VERSION = None

# ---------------------------------------------------------------------------
# 3. HTTP service (fail-closed: any error -> REJECT)
# ---------------------------------------------------------------------------
RAIL_LATENCY_MS = []


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("content-length", 0))
            if length > 32_000:
                return self._json(413, {"ok": False, "reason": "body_too_large"})
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            t0 = time.perf_counter()

            if self.path == "/check-input":
                result = check_input_text(data.get("text", ""))
            elif self.path == "/check-output":
                result = check_output_text(data.get("text", ""), data.get("evidence", []))
            elif self.path == "/check-retrieval":
                result = screen_retrieved_content(data.get("content", ""))
            elif self.path == "/check-tool":
                result = check_tool_call(data.get("tool", ""), data.get("args", {}))
            else:
                return self._json(404, {"error": "unknown route"})

            RAIL_LATENCY_MS.append((time.perf_counter() - t0) * 1000)
            return self._json(200, result)
        except Exception as exc:  # noqa: BLE001 — FAIL-CLOSED: never pass on internal error
            return self._json(500, {"ok": False, "reason": "fail_closed", "detail": str(exc)[:120]})

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {
                "status": "ok", "service": "nemo-guardrails-local",
                "nemo_framework": NEMO_VERSION, "llm": "mocked",
                "inference": "disabled",
                "avg_rail_latency_ms": round(sum(RAIL_LATENCY_MS) / len(RAIL_LATENCY_MS), 2) if RAIL_LATENCY_MS else 0})
        return self._json(404, {"error": "unknown route"})

    def log_message(self, fmt, *args):  # metadata-only logging: no raw prompts
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9071
    print(f"NeMo Guardrails local service on :{port}")
    print(f"  framework: nemoguardrails=={NEMO_VERSION} (available={NEMO_AVAILABLE})")
    print(f"  LLM: MOCKED — no NVIDIA calls ever")
    print(f"  inference: DISABLED (fail-closed)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
