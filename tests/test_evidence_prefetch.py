"""Offline tests for the deterministic evidence pre-retrieval (Phase 6
optimization — PREPARED, NOT DEPLOYED; default gate OFF). All tools are
mocked; nothing touches AWS/Pinecone/NIM."""
from unittest.mock import patch

import src.evidence_prefetch as ep


def test_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_EVIDENCE_PREFETCH", raising=False)
    assert ep.prefetch_enabled() is False
    monkeypatch.setenv("ENABLE_EVIDENCE_PREFETCH", "true")
    assert ep.prefetch_enabled() is True


def test_shipment_id_extraction_deterministic():
    assert ep.extract_shipment_ids("What happened to shipment UC-003241?") == ["UC-003241"]
    assert ep.extract_shipment_ids("compare uc-003241 and UC-999999") == ["UC-003241", "UC-999999"]
    assert ep.extract_shipment_ids("no ids here") == []
    assert ep.extract_shipment_ids("") == []
    assert ep.extract_shipment_ids("UC-123 and UC-1234567 are not unified ids") == []


def test_sop_query_selection_is_fixed_templates():
    assert "corrective action" in ep.sop_query_for("what corrective action does the SOP recommend for the excursion")
    assert "sensor" in ep.sop_query_for("the temperature logger stopped reporting")
    assert "custody" in ep.sop_query_for("how is the handover seal verified")
    assert "humidity" in ep.sop_query_for("humidity deviation handling")
    assert ep.sop_query_for("random unrelated question") == ep.DEFAULT_SOP_QUERY


def test_prefetch_sql_is_guard_valid():
    """The FIXED pre-retrieval queries must pass the production SQL guard —
    guaranteeing the pre-fetch can never sidestep guard rules."""
    from src.sql_guard import validate_query
    for sid in ("UC-003241", "UC-999999"):
        validate_query(ep.INCIDENT_SQL.format(sid=sid))
        validate_query(ep.NOTIFICATION_SQL.format(sid=sid))


def test_prefetch_evidence_requires_complete_citation():
    """Regression (operator directive 2026-11-17): the evidence block must
    EXPLICITLY demand the complete bracketed SOP citation (title, version,
    section, synthetic status) — Lightning's first synthesis lost the
    title/version and the validator correctly rejected the answer.
    Updated 2026-11-21 (M2 hardening): the demand is now the 'SOP RULES' line
    plus the explicit OUTPUT CONTRACT (no tool names/view names/SQL in the
    answer)."""
    from unittest.mock import patch as _patch

    def fake_invoke(tool, payload):
        return "tool output"
    with _patch.object(ep, "_invoke_tool", fake_invoke):
        evidence = ep.build_evidence(
            "UC-003241 what corrective action does the SOP recommend")
    assert "COMPLETE bracketed SOP citation verbatim" in evidence
    assert "natural-language answer" in evidence
    assert "NEVER write tool names" in evidence
    assert "INTENT: incident_sop" in evidence


def test_prefetch_node_runs_tools_and_injects_evidence(monkeypatch):
    from langchain_core.messages import HumanMessage
    monkeypatch.setenv("ENABLE_EVIDENCE_PREFETCH", "true")
    call_count = {"n": 0}
    def fake_invoke(tool, payload):
        call_count["n"] += 1
        return "tool output"
    with patch.object(ep, "_invoke_tool", fake_invoke):
        state = {"messages": [HumanMessage(content="Shipment UC-003241 reached 6.2C — what corrective action does the SOP recommend?")]}
        out = ep.prefetch_node(state)
    assert len(out["messages"]) == 1
    evidence = out["messages"][0].content
    assert "UC-003241" in evidence and "PRE-FETCHED EVIDENCE" in evidence
    assert "SHIPMENT RECORD" in evidence  # new: shipment record retrieved
    assert "RECENT TELEMETRY" in evidence  # new: telemetry retrieved
    assert "SOP GUIDANCE" in evidence  # SOP intent matched (corrective action)
    assert call_count["n"] >= 5  # 5 SQL + 1 SOP = 6 tool calls


def test_prefetch_node_noop_without_shipment_id(monkeypatch):
    from langchain_core.messages import HumanMessage
    monkeypatch.setenv("ENABLE_EVIDENCE_PREFETCH", "true")
    state = {"messages": [HumanMessage(content="What is the SOP for humidity?")]}
    assert ep.prefetch_node(state) == {}


def test_prefetch_node_noop_when_gate_off(monkeypatch):
    from langchain_core.messages import HumanMessage
    monkeypatch.setenv("ENABLE_EVIDENCE_PREFETCH", "false")
    state = {"messages": [HumanMessage(content="shipment UC-003241 incident")]}
    assert ep.prefetch_node(state) == {}


def test_prefetch_failure_states_limitation_not_crash(monkeypatch):
    from langchain_core.messages import HumanMessage
    monkeypatch.setenv("ENABLE_EVIDENCE_PREFETCH", "true")

    def boom(tool, payload):
        raise RuntimeError("db down")
    with patch.object(ep, "_invoke_tool", boom):
        evidence = ep.build_evidence("UC-003241 excursion — what should be done?")
    assert "Retrieval limitation" in evidence and "SOP retrieval limitation" in evidence
    assert "RuntimeError" in evidence


def test_graph_wiring_gated_by_env():
    """Default OFF: the stock ReAct flow (START -> reasoner) is compiled;
    the prefetch node exists only when the operator enables it."""
    import inspect
    import src.orchestrator as orch
    source = inspect.getsource(orch)
    assert "if prefetch_enabled():" in source
    assert "graph_builder.add_edge(START, \"prefetch\")" in source
    assert 'graph_builder.add_edge(START, "reasoner")' in source


def test_no_extra_model_calls_by_construction():
    """The pre-retrieval adds ZERO model calls: it runs only the two existing
    tools (deterministic inputs) and appends evidence — the NIM synthesis is
    the single remaining turn."""
    import inspect
    source = inspect.getsource(ep)
    for forbidden in ("create_llm", "ChatOpenAI", "llm.invoke", "openai"):
        assert forbidden not in source
