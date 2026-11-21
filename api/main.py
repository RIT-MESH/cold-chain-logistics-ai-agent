"""OpenAI-compatible chat endpoint for LibreChat.

Run from project root: uvicorn api.main:app --host 127.0.0.1 --port 8000
The existing LangGraph agent and its tools are loaded on the first chat request.

2026-11-21 LibreChat repair (operator directive): intent-based validator
dispatch (M3), one bounded corrective synthesis retry (M2), a deterministic
evidence-only fallback and meaningful OpenAI-format errors instead of blank
502s (M1/§8), and conversation-context-aware prefetch scoping (§3).
"""
import json
import logging
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.audit import audit_request_end, current_counters, emit_audit, new_request

MODEL = "cold-chain-agent"
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
PROMPT = (ROOT / "src/prompts/system_prompt.txt").read_text(encoding="utf-8")
# uvicorn does NOT configure the root logger, so third-party INFO loggers
# (cold-chain.audit, cold-chain.gateway) were silently dropped (live-found:
# zero audit lines in CloudWatch after the first Phase 5 E2E; pre-pause
# finding C13-F3). One root handler makes the structured audit JSON lines
# flow to stdout -> awslogs -> /coldchain/dev/gateway.
logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
app = FastAPI(title="Cold-Chain Agent Gateway", version="0.2.0")
LOGGER = logging.getLogger("cold-chain.gateway")

# The strict Phase 6 incident+SOP validator is evidence-bound to the
# UC-003241 demo case (its ground-truth package). Other shipments use the
# general shipment validator; the dispatch itself is INTENT-driven (M3 fix).
STRICT_VALIDATOR_SHIPMENT = "UC-003241"


class ChatMessage(BaseModel):
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | None = None


class ChatRequest(BaseModel):
    model: str = MODEL
    messages: list[ChatMessage] = Field(min_length=1, max_length=60)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


def require_key(authorization: str | None = Header(default=None)) -> None:
    configured = os.getenv("FDE_API_KEY", "")
    if not configured:
        raise HTTPException(503, "FDE_API_KEY is not configured")
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(value, configured):
        # Phase 5 (§7): authorization failures are audited — actor stays
        # pseudonymous, no token material is ever recorded.
        emit_audit("auth_failure", provided_scheme=scheme.lower() or "none")
        raise HTTPException(401, "Invalid API key", headers={"WWW-Authenticate": "Bearer"})


@app.get("/health")
def health():
    return {"status": "ok", "service": "cold-chain-gateway"}


@app.get("/v1/models", dependencies=[Depends(require_key)])
def models():
    return {"object": "list", "data": [{"id": MODEL, "object": "model", "created": 0, "owned_by": "cold-chain"}]}


def convert_history(messages: list[ChatMessage]):
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

    # Never accept caller-controlled system prompts or tool-result impersonation.
    converted = [SystemMessage(content=PROMPT)]
    for message in messages:
        if message.role == "user":
            converted.append(HumanMessage(content=message.content or ""))
        elif message.role == "assistant" and message.content:
            converted.append(AIMessage(content=message.content))
        # System, developer and tool messages are deliberately ignored.
    if not converted or not isinstance(converted[-1], HumanMessage):
        raise HTTPException(400, "The final message must be from the user")
    return converted


def _timeout_class(exc: BaseException) -> str:
    """Classify an agent failure for structured audit + user messaging."""
    seen = set()
    node = exc
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        name = type(node).__name__
        if "Timeout" in name:
            return "nvidia_timeout"
        if "AuthenticationError" in name or "401" in str(getattr(node, "status_code", "")):
            return "nvidia_auth"
        if getattr(node, "status_code", None) == 404:
            return "nvidia_model_unavailable"
        node = node.__cause__ or node.__context__
    return "agent_error"


def _error_response(status: int, message: str, err_type: str, code: str) -> JSONResponse:
    """OpenAI-compatible error body (top-level 'error' object, the shape the
    OpenAI client in LibreChat parses) so the user sees a meaningful message
    instead of a blank response (directive §8/M1-req4)."""
    return JSONResponse(status_code=status, content={"error": {
        "message": message, "type": err_type, "param": "", "code": code}})


def _resolve_request(messages: list[ChatMessage]):
    """Deterministic context resolution + intent for THIS request (§3).
    The same function drives the prefetch node, so gateway dispatch and
    evidence scope can never disagree."""
    from src.context_resolver import resolve_conversation
    return resolve_conversation(convert_history(messages))


def _prefetch_markers(result_messages) -> dict:
    """Read the machine-readable markers our own prefetch SystemMessage
    wrote (SOP availability). User text never becomes that message."""
    from src.evidence_prefetch import (
        PREFETCH_MARKER, SOP_AVAILABLE_MARK, SOP_UNAVAILABLE_MARK,
    )
    for m in result_messages:
        content = str(getattr(m, "content", "") or "")
        if getattr(m, "type", "") == "system" and PREFETCH_MARKER in content:
            return {
                "had_prefetch": True,
                "sop_available": SOP_AVAILABLE_MARK in content,
                "sop_unavailable": SOP_UNAVAILABLE_MARK in content,
            }
    return {"had_prefetch": False, "sop_available": False, "sop_unavailable": False}


def _validate_answer(answer: str, resolution, sop_available: bool) -> dict:
    """Intent-based validator dispatch (M3 fix: never dispatched by the
    shipment id that happens to appear in the answer)."""
    from src.answer_validator import (
        validate_answer, validate_comparison_answer, validate_history_answer,
        validate_shipment_answer,
    )
    intent = resolution.intent
    if intent == "incident_sop" and resolution.active_id == STRICT_VALIDATOR_SHIPMENT:
        return validate_answer(answer, sop_available=sop_available)
    if intent == "comparison" and len(resolution.ids) >= 2:
        return validate_comparison_answer(answer, resolution.ids[0], resolution.ids[1])
    if intent in ("temperature_history", "humidity_history"):
        return validate_history_answer(answer, resolution.active_id)
    return validate_shipment_answer(answer, resolution.active_id)


def _deterministic_fallback(resolution, question: str):
    """Evidence-only fallback (§8): fresh fixed SQL, clearly labeled,
    validated by the same intent-based validator. Returns the answer text or
    None (insufficient evidence)."""
    from src.evidence_summary import deterministic_answer
    fallback = deterministic_answer(resolution.ids, resolution.intent, question)
    if fallback is None:
        return None
    verdict = _validate_answer(fallback, resolution, sop_available=True)
    if not verdict["passed"]:
        emit_audit("fallback_validation_rejected", failed_checks=verdict["failures"])
        return None
    return fallback


def _last_user_text(messages: list[ChatMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user" and m.content:
            return m.content
    return ""


def get_answer(messages: list[ChatMessage]) -> tuple[str, dict]:
    from src.orchestrator import fde_agent
    # Unique run avoids cross-user MemorySaver state leakage. The full chat
    # history is sent by LibreChat on each request.
    result = fde_agent.invoke(
        {"messages": convert_history(messages)},
        config={"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 20},
    )
    # C12-style token counter (R6-1): NVIDIA NIM exposes no credit-balance
    # API (verification finding F-4), so the per-response usage object is
    # the only consumption signal. Summed over every model turn of the ReAct
    # loop; reasoning tokens tracked separately because they consume the
    # completion budget (F-3).
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
    from langchain_core.messages import AIMessage
    for item in result["messages"]:
        if isinstance(item, AIMessage):
            meta = getattr(item, "usage_metadata", None) or {}
            usage["prompt_tokens"] += int(meta.get("input_tokens") or 0)
            usage["completion_tokens"] += int(meta.get("output_tokens") or 0)
            # LangChain exposes the reasoning breakdown as
            # usage_metadata.output_token_details.reasoning — verified live
            # 2026-11-08 (probe in docs/evidence/NVIDIA_NIM_LATENCY_RESULTS.json).
            details = meta.get("output_token_details") or {}
            usage["reasoning_tokens"] += int(details.get("reasoning") or 0)
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    markers = _prefetch_markers(result["messages"])
    for item in reversed(result["messages"]):
        if isinstance(item, AIMessage) and item.content and not item.tool_calls:
            if isinstance(item.content, str):
                return item.content, {**usage, **markers}
            return str(item.content), {**usage, **markers}
    raise RuntimeError("Agent returned no final assistant response")


@app.post("/v1/chat/completions", dependencies=[Depends(require_key)])
async def chat(request: ChatRequest):
    if request.model != MODEL:
        raise HTTPException(400, f"Unknown model: {request.model}")
    # Explicit limits to prevent excessive tool work and unbounded prompts.
    if sum(len(m.content or "") for m in request.messages) > 60000:
        raise HTTPException(413, "Conversation too long")
    messages = request.messages
    # Phase 5: one audited scope per request — stable correlation id that
    # every tool-level audit line inherits (ContextVar; run_in_threadpool
    # copies it into the agent thread). Actor is a pseudonymous key hash.
    request_id = new_request(os.getenv("FDE_API_KEY"))
    started = time.monotonic()

    resolution = _resolve_request(messages)
    question = _last_user_text(messages)

    def _duration_ms():
        return int((time.monotonic() - started) * 1000)

    def _finish(answer: str, meta: dict, source: str):
        emit_audit("answer_source", source=source, intent=resolution.intent)
        audit_request_end(status="success", duration_ms=_duration_ms(), model=MODEL,
                          tool_events=current_counters().get("tool_executions"))
        usage = {k: meta.get(k, 0) for k in
                 ("prompt_tokens", "completion_tokens", "total_tokens")}
        created = int(time.time())
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        if not request.stream:
            return {
                "id": completion_id, "object": "chat.completion", "created": created,
                "model": MODEL,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
                "usage": usage,
            }

        # A valid SSE response, currently delivered as a single completion chunk
        # after the underlying synchronous agent returns. Not token-by-token streaming.
        def event_stream():
            def chunk(delta, finish_reason=None):
                return "data: " + json.dumps({
                    "id": completion_id, "object": "chat.completion.chunk", "created": created,
                    "model": MODEL,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                }) + "\n\n"
            yield chunk({"role": "assistant"})
            yield chunk({"content": answer})
            yield chunk({}, "stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    def _fail(status: int, message: str, err_type: str, code: str, audit_status: str,
              error_class: str | None = None):
        audit_kwargs = {"status": audit_status, "duration_ms": _duration_ms(), "model": MODEL}
        if error_class:
            audit_kwargs["error_class"] = error_class
        audit_request_end(**audit_kwargs)
        return _error_response(status, message, err_type, code)

    # ---------------------------------------------------------------- attempt 1
    try:
        answer, meta = await run_in_threadpool(get_answer, messages)
    except HTTPException:
        raise
    except Exception as exc:
        LOGGER.exception("Agent execution failed")
        error_class = _timeout_class(exc)
        audit_request_end(status="error", duration_ms=_duration_ms(), model=MODEL,
                          error_class=error_class)
        # Deterministic evidence-only fallback for SUPPORTED shipment
        # questions (§8); meaningful unavailability message otherwise.
        if resolution.ids:
            try:
                fallback = await run_in_threadpool(
                    _deterministic_fallback, resolution, question)
            except Exception:  # noqa: BLE001 - fallback is best-effort
                fallback = None
            if fallback is not None:
                return _finish(fallback, {}, "deterministic_fallback")
            return _error_response(
                503,
                "The AI service is not responding within the bounded time limit, and the "
                "deterministic evidence summary could not be fully assembled or validated. "
                "The database itself is healthy — please retry in a few minutes, or ask "
                "about a specific shipment ID (e.g. 'report for UC-003916').",
                "service_unavailable", "nvidia_unavailable",
            )
        return _error_response(
            503,
            "The AI reasoning service did not respond within the bounded time limit after "
            "retries (NVIDIA serving is degraded). Shipment questions with a shipment ID "
            "(for example 'report for UC-003916') can still be answered from the database "
            "evidence; please retry general questions in a few minutes.",
            "service_unavailable", "nvidia_timeout",
        )

    # ---------------------------------------------------------------- validation
    if meta.get("had_prefetch") and resolution.ids:
        sop_available = bool(meta.get("sop_available"))
        verdict = _validate_answer(answer, resolution, sop_available)
        if not verdict["passed"]:
            emit_audit("synthesis_validation_rejected",
                       failed_checks=verdict["failures"], attempt=1)
            # M2: ONE bounded corrective generation attempt — never more.
            try:
                answer, meta = await run_in_threadpool(get_answer, messages)
                sop_available = bool(meta.get("sop_available"))
                verdict = _validate_answer(answer, resolution, sop_available)
                if not verdict["passed"]:
                    emit_audit("synthesis_validation_rejected",
                               failed_checks=verdict["failures"], attempt=2)
            except HTTPException:
                raise
            except Exception as exc:
                LOGGER.exception("Agent execution failed on corrective retry")
                audit_request_end(status="error", duration_ms=_duration_ms(), model=MODEL,
                                  error_class=_timeout_class(exc))
                verdict = None  # fall through to the deterministic fallback
            if verdict is None or not verdict["passed"]:
                fallback = await run_in_threadpool(
                    _deterministic_fallback, resolution, question)
                if fallback is not None:
                    return _finish(fallback, meta, "deterministic_fallback_after_rejection")
                return _fail(502,
                             "The answer failed evidence validation and could not be corrected "
                             "automatically; the evidence summary was also incomplete. No "
                             "unvalidated answer is returned. Please rephrase and retry.",
                             "validation_error", "answer_validation",
                             "validation_rejected")

    return _finish(answer, meta, "synthesis")
