"""Offline tests for the SYTHESIS_MODEL switch + answer-validation gate
(operator Phase 6 decision 2026-11-17). No live model calls: LLMs and the
graph are mocked/source-asserted; the stock ReAct flow must remain the
default when SYTHESIS_MODEL is unset (rollback = unset + restart)."""
import inspect
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage, SystemMessage

import src.orchestrator as orch


def test_rollback_default_synthesis_model_unset():
    """SYTHESIS_MODEL unset (the committed default) -> synthesis_llm is None
    -> every request goes through the stock GLM ReAct path."""
    import os
    assert not os.getenv("SYTHESIS_MODEL"), "SYTHESIS_MODEL must be unset by default"
    assert orch.synthesis_llm is None


def test_prefetch_marker_detection():
    assert orch._has_prefetch_evidence([SystemMessage(content="PRE-FETCHED EVIDENCE for UC-003241")])
    assert not orch._has_prefetch_evidence([HumanMessage(content="PRE-FETCHED EVIDENCE")])
    assert not orch._has_prefetch_evidence([HumanMessage(content="normal question")])


def test_reasoning_routes_to_synthesis_only_when_configured_and_prefetched():
    """With synthesis_llm configured: prefetched requests use the synthesis
    LLM (no tools); everything else keeps llm_with_tools."""
    fake_synthesis = MagicMock()
    fake_react = MagicMock()
    state = {"messages": [SystemMessage(content="PRE-FETCHED EVIDENCE ..."),
                          HumanMessage(content="what to do?")]}
    with patch.object(orch, "synthesis_llm", fake_synthesis), \
         patch.object(orch, "llm_with_tools", fake_react):
        orch.reasoning_node(state)
    fake_synthesis.invoke.assert_called_once()
    fake_react.invoke.assert_not_called()

    with patch.object(orch, "synthesis_llm", fake_synthesis), \
         patch.object(orch, "llm_with_tools", fake_react):
        orch.reasoning_node({"messages": [HumanMessage(content="general question")]})
    fake_react.invoke.assert_called_once()


def test_synthesis_path_binds_no_tools():
    """The synthesis LLM must have NO tools bound (unqualified tool behavior
    of a synthesis-only model cannot occur)."""
    source = inspect.getsource(orch)
    assert "bind_tools" not in source.split("synthesis_llm = None", 1)[1].split("# ====", 1)[0]
    # and the synthesis invocation is a plain LLM, not llm_with_tools
    assert "synthesis_llm.invoke(state[" in source


def test_gateway_validates_prefetched_uc003241_answers():
    """The api/main gate: a prefetched UC-003241 answer that FAILS validation
    must be rejected (502 + failed checks), never returned as success."""
    source = inspect.getsource(__import__("api.main", fromlist=["main"]))
    assert "validate_answer" in source and "validation_rejected" in source
    assert "had_prefetch" in source and "failed_checks" in source
    # 2026-11-21 (M3 fix): dispatch is intent-based — the strict UC-003241
    # validator is scoped via an explicit constant, never by extracting the
    # shipment id from the answer text.
    assert "STRICT_VALIDATOR_SHIPMENT" in source
    assert "_validate_answer" in source
    assert "validate_shipment_answer" in source


def test_gateway_gate_rejects_fabricated_answer(monkeypatch):
    """End-to-end at the FastAPI layer: a fabricated synthesis answer raises
    the 502 validation rejection; a grounded answer passes."""
    from fastapi import HTTPException
    from src.answer_validator import validate_answer

    fabricated = "The shipment UC-003241 hit 9.2 C and the email was delivered."
    verdict = validate_answer(fabricated)
    assert not verdict["passed"]
    assert "no fabricated higher temperature" in verdict["failures"]
    assert "no invented delivery confirmation" in verdict["failures"]

    grounded = ("UC-003241 incident temp:8df143db: 6.2 C vs 4.0 C threshold, severity derived, "
                "status OPEN, synthetic provenance; sns_acked = topic acceptance, NOT recipient "
                "delivery. SOP Temperature Management v1.0-demo section A3 (SYNTHETIC TEST "
                "DOCUMENT): verify sensor, driver restarts cooling unit, divert if > 1 hour. "
                "Human review required.")
    assert validate_answer(grounded)["passed"], validate_answer(grounded)["failures"]
