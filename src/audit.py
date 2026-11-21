"""Structured agent-activity auditing (Phase 5, blueprint ADR-10).

Every audit record is ONE JSON line on the dedicated `cold-chain.audit`
logger (UTC ISO-8601, stable correlation id, explicit event classification).
The line ships to container stdout and from there to the existing
/coldchain/dev/gateway CloudWatch Logs group via the compose `awslogs`
driver (non-blocking — audit must never stall the chat path).

Privacy rules (operator directive §7):
- NO raw user questions, NO model responses, NO SQL text with sensitive
  literals: only the guard DECISION, the approved view(s), shapes and
  outcomes. SQL query text is logged ONLY when guard-rejected, and only
  its normalized fingerprint + error class.
- NO tokens, session cookies, passwords or DB credentials — the actor is
  a pseudonymous hash prefix of the gateway API key.
- Logging/metric failures are swallowed: auditing must never break a chat
  request (honest loss: a failed audit emission is NOT retried).

Aggregates are emitted to CloudWatch under the EXISTING namespace
ColdChain/Phase4 (the only namespace the app-host role may write) at the
end of each request: AgentRequests / AgentToolExecutions /
AgentGuardRejections / AgentErrors. Together with the two reconcile
metrics these stay inside CloudWatch's 10-free-custom-metrics tier.
"""
import hashlib
import json
import logging
import os
import time
import uuid
from contextvars import ContextVar

AUDIT_LOGGER = logging.getLogger("cold-chain.audit")
METRIC_NAMESPACE = "ColdChain/Phase4"

_request_id: ContextVar = ContextVar("fde_request_id", default=None)
_actor: ContextVar = ContextVar("fde_actor", default=None)
_counters: ContextVar = ContextVar("fde_audit_counters", default=None)

COUNTER_KEYS = ("requests", "tool_executions", "guard_rejections", "errors", "sop_retrievals")

# Substrings that must never appear in an audit record. Applied as a final
# defense to every emitted JSON line.
_SECRET_MARKERS = ("nvapi-", "password", "secret", "authorization:", "bearer ", "cookie")


def new_request(actor_key: str | None) -> str:
    """Start an audited request scope: fresh correlation id, stable
    pseudonymous actor, zeroed counters. Returns the request id."""
    _request_id.set(str(uuid.uuid4()))
    _actor.set(_pseudonymize(actor_key))
    _counters.set({k: 0 for k in COUNTER_KEYS})
    return _request_id.get()


def current_request_id() -> str:
    return _request_id.get() or "unscoped"


def current_counters() -> dict:
    """Read-only view of the current request's audit counters (gateway
    request-end event)."""
    return dict(_counters.get() or {})


def _pseudonymize(value: str | None) -> str:
    """Stable, non-reversible actor label (never the raw key)."""
    if not value:
        return "anonymous"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"key-{digest}"


def _redact(text: str) -> str:
    """Drop anything carrying secret markers from a JSON line entirely."""
    low = text.lower()
    if any(marker in low for marker in _SECRET_MARKERS):
        return "[redacted]"
    return text


def _counter(step: str, amount: int = 1) -> None:
    counters = _counters.get()
    if counters is not None and step in counters:
        counters[step] += amount


def emit_audit(event_type: str, **fields) -> None:
    """Emit ONE structured audit line. Failures are swallowed by design
    (the request path must not depend on auditing)."""
    try:
        record = {
            "event": event_type,
            "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "request_id": current_request_id(),
            "actor": _actor.get() or "anonymous",
        }
        record.update({k: v for k, v in fields.items() if v is not None})
        AUDIT_LOGGER.info(_redact(json.dumps(record, default=str, sort_keys=True)))
    except Exception:  # noqa: BLE001 - audit must never raise
        pass


def audit_tool_event(tool: str, outcome: str, *, duration_ms: int | None = None,
                     guard_decision: str | None = None, views: list | None = None,
                     rows: int | None = None, error_class: str | None = None,
                     sql_fingerprint: str | None = None,
                     documents: list | None = None, cited: bool | None = None) -> None:
    """Uniform tool-execution audit event (§7 field set). Phase 6 adds
    `documents` (retrieved document ids) and `cited` (citation included) for
    SOP retrieval events (operator directive §10)."""
    if outcome == "executed":
        _counter("tool_executions")
        if tool == "search_compliance_sop":
            _counter("sop_retrievals")  # Phase 6: SOP retrieval visibility (Grafana panel)
    elif outcome == "guard_rejected":
        _counter("guard_rejections")
    elif outcome == "error":
        _counter("errors")
    emit_audit("agent_tool_event", tool=tool, outcome=outcome,
               duration_ms=duration_ms, guard_decision=guard_decision,
               views=views, rows=rows, error_class=error_class,
               sql_fingerprint=sql_fingerprint, documents=documents, cited=cited)


def audit_request_end(*, status: str, duration_ms: int, model: str,
                      tool_events: int | None = None,
                      error_class: str | None = None) -> None:
    """Request completion audit event + CloudWatch aggregate emission.
    error_class (2026-11-21): structured failure classification for error
    statuses (nvidia_timeout / nvidia_auth / nvidia_model_unavailable /
    agent_error) — required for the M1 degradation analysis."""
    _counter("requests")
    if status == "error":
        _counter("errors")
    emit_audit("agent_request_end", status=status, duration_ms=duration_ms,
               model=model, tool_events=tool_events, error_class=error_class)
    put_request_metrics()


def put_request_metrics() -> None:
    """Emit the request's aggregate counters to CloudWatch under the
    namespace the app-host role is already allowed to write. Silent on
    every failure (audit best-effort; CloudWatch metric free tier ≤ 10
    custom metrics — currently 2 reconcile + 4 agent metrics)."""
    counters = _counters.get()
    if not counters or not any(counters.values()):
        return
    try:
        import boto3
        cw = boto3.client("cloudwatch", region_name=os.getenv("AWS_REGION", "us-east-1"))
        cw.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=[
            {"MetricName": "AgentRequests", "Value": counters.get("requests", 0), "Unit": "Count"},
            {"MetricName": "AgentToolExecutions", "Value": counters.get("tool_executions", 0), "Unit": "Count"},
            {"MetricName": "AgentGuardRejections", "Value": counters.get("guard_rejections", 0), "Unit": "Count"},
            {"MetricName": "AgentErrors", "Value": counters.get("errors", 0), "Unit": "Count"},
            {"MetricName": "AgentSopRetrievals", "Value": counters.get("sop_retrievals", 0), "Unit": "Count"},
        ])
    except Exception:  # noqa: BLE001 - never break the chat path
        pass


def sql_fingerprint(sql: str) -> str:
    """Normalized, structure-only query fingerprint for audit records:
    whitespace-collapsed, values elided by length — enough to correlate a
    rejection pattern without retaining literal SQL (§7)."""
    import re
    collapsed = " ".join((sql or "").split())
    collapsed = re.sub(r"'[^']*'", "'…'", collapsed)
    collapsed = re.sub(r"[0-9]+", "n", collapsed)
    return (collapsed[:120] + "…") if len(collapsed) > 120 else collapsed


# Audit event classification (§8) — the stable vocabulary every record's
# `event` field is drawn from.
EVENT_TYPES = {
    "agent_request_end": "request completion (status, duration, model, tool count)",
    "agent_tool_event": "tool execution / guard rejection / error (per invocation)",
    "auth_failure": "gateway API-key authentication failure (401)",
}
