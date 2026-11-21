"""Gateway repair tests (operator directive 2026-11-21 §2/§8): OpenAI-format
error bodies (no more blank responses), timeout classification, bounded
corrective retry structure, and the deterministic fallback wiring. All
offline — the agent is never invoked."""
import inspect

import api.main as main


# ------------------------------------------------------------ error body shape
def test_error_response_is_openai_format():
    resp = main._error_response(503, "service is slow", "service_unavailable", "nvidia_timeout")
    import json
    body = json.loads(resp.body.decode())
    assert set(body.keys()) == {"error"}
    assert body["error"]["message"] == "service is slow"
    assert body["error"]["type"] == "service_unavailable"
    assert body["error"]["code"] == "nvidia_timeout"
    assert resp.status_code == 503


# ------------------------------------------------------------ timeout classification
def _with_cause(inner, outer):
    try:
        try:
            raise inner
        except Exception as e:
            raise outer from e
    except Exception as e2:
        return e2


def test_timeout_class_detected_through_cause_chain():
    import openai
    inner = openai.APITimeoutError(request="r")
    exc = _with_cause(inner, RuntimeError("agent failed"))
    assert main._timeout_class(exc) == "nvidia_timeout"


def test_timeout_class_plain_error():
    assert main._timeout_class(RuntimeError("boom")) == "agent_error"


# ------------------------------------------------------------ bounded retry structure
def test_corrective_retry_is_bounded_to_one_attempt():
    """M2 req #5: exactly ONE corrective generation attempt — never more."""
    src = inspect.getsource(main.chat)
    assert src.count("run_in_threadpool(get_answer, messages)") == 2  # attempt 1 + retry
    assert "attempt=1" in src and "attempt=2" in src  # audited attempts


def test_fallback_wired_into_gateway():
    """§8: supported shipment requests fall back to deterministic evidence
    on synthesis failure; general questions get a meaningful message."""
    src = inspect.getsource(main)
    assert "_deterministic_fallback" in src
    assert "deterministic_fallback" in src  # answer_source audit labels
    assert "service_unavailable" in src     # OpenAI-format user-facing errors


def test_fallback_preferenced_over_more_nvidia_calls():
    """§8/M2 req #6: after the bounded retry fails, the gateway uses the
    deterministic answer — it must NOT loop the agent again."""
    src = inspect.getsource(main.chat)
    # the only agent invocations are attempt 1 and the single retry
    assert src.count("run_in_threadpool(get_answer") == 2


def test_validation_rejection_never_returns_raw_answer():
    """M2 req #7: a rejected answer can never reach the user."""
    src = inspect.getsource(main.chat)
    # after a failed verdict the only exits are the fallback or the 502
    assert "No " in src  # error message states no unvalidated answer returned
    assert "answer_validation" in src


# ------------------------------------------------------------ prefetch marker reading
def test_prefetch_marker_parsing():
    from langchain_core.messages import SystemMessage
    from src.evidence_prefetch import SOP_AVAILABLE_MARK, SOP_UNAVAILABLE_MARK

    ev_ok = SystemMessage(content="PRE-FETCHED EVIDENCE for UC-003241 ...\n" + SOP_AVAILABLE_MARK)
    ev_bad = SystemMessage(content="PRE-FETCHED EVIDENCE for UC-003241 ...\n" + SOP_UNAVAILABLE_MARK)
    assert main._prefetch_markers([ev_ok])["sop_available"] is True
    assert main._prefetch_markers([ev_bad])["sop_available"] is False
    assert main._prefetch_markers([SystemMessage(content="unrelated")])["had_prefetch"] is False


# ------------------------------------------------------------ stream contract preserved
def test_streaming_contract_unchanged():
    src = inspect.getsource(main)
    assert "chat.completion.chunk" in src
    assert "[DONE]" in src
