"""LibreChat general-shipment fix tests (operator directive 2026-11-18):
the raw tool-call representation was being returned as a "successful"
answer. These tests verify the extended prefetch (shipment record +
telemetry + custody), the intent-conditional SOP retrieval, the general
shipment validator's rejection of raw tool-call syntax, and the guard
validity of all new fixed SQL queries."""
import re
from unittest.mock import patch

import pytest

import src.evidence_prefetch as ep
from src.answer_validator import RAW_TOOL_CALL_RE, validate_shipment_answer
from src.sql_guard import validate_query

# ---------------------------------------------- new SQL queries guard-valid
def test_shipment_sql_guard_valid():
    for sid in ("UC-003916", "UC-003241", "UC-999999"):
        validate_query(ep.SHIPMENT_SQL.format(sid=sid))
        validate_query(ep.TELEMETRY_SQL.format(sid=sid))
        validate_query(ep.TELEMETRY_STATS_SQL.format(sid=sid))
        validate_query(ep.CUSTODY_SQL.format(sid=sid))
        validate_query(ep.INCIDENT_SQL.format(sid=sid))
        validate_query(ep.NOTIFICATION_SQL.format(sid=sid))


# ---------------------------------------------- SOP intent gating (2026-11-21:
# intent classification moved to src/context_resolver.py — deterministic,
# question-only, no model involvement)
def test_sop_not_retrieved_for_general_shipment_question():
    from src.context_resolver import _classify_intent
    assert _classify_intent("give me a detailed report of shipment UC-003916", ["UC-003916"]) == "shipment_report"
    assert _classify_intent("what is the status of UC-003916", ["UC-003916"]) == "shipment_report"
    assert _classify_intent("what happened to shipment UC-003241", ["UC-003241"]) == "incident"


def test_sop_retrieved_for_corrective_action_question():
    from src.context_resolver import _classify_intent
    assert _classify_intent("what corrective action does the SOP recommend for UC-003241", ["UC-003241"]) == "incident_sop"
    assert _classify_intent("the temperature logger stopped reporting on UC-003241", ["UC-003241"]) == "incident_sop"


# ---------------------------------------------- evidence package content
def test_evidence_contains_shipment_record_and_telemetry(monkeypatch):
    monkeypatch.setenv("ENABLE_EVIDENCE_PREFETCH", "true")
    calls = []

    def fake_invoke(tool, payload):
        calls.append(payload.get("sql_query", payload.get("query", "")))
        return "tool output for: " + str(payload.get("sql_query", payload.get("query", "")))[:60]

    with patch.object(ep, "_invoke_tool", fake_invoke):
        evidence = ep.build_evidence("give me a detailed report of shipment UC-003916")
    assert evidence is not None
    assert "UC-003916" in evidence
    assert "SHIPMENT RECORD" in evidence
    assert "RECENT TELEMETRY" in evidence
    assert "CUSTODY HANDOFFS" in evidence
    assert "INCIDENT EVIDENCE" in evidence
    # SOP NOT retrieved (no intent match)
    assert "SOP GUIDANCE" not in evidence
    # the shipment + telemetry queries actually ran
    assert any("VW_SHIPMENTS" in c for c in calls)
    assert any("VW_TELEMETRY" in c and "UC-003916" in c for c in calls)


def test_evidence_includes_sop_for_corrective_question(monkeypatch):
    monkeypatch.setenv("ENABLE_EVIDENCE_PREFETCH", "true")

    def fake_invoke(tool, payload):
        return "tool output"
    with patch.object(ep, "_invoke_tool", fake_invoke):
        evidence = ep.build_evidence(
            "UC-003241 reached 6.2C what corrective action does the SOP recommend")
    assert "SOP GUIDANCE" in evidence


# ---------------------------------------------- validator: raw tool-call rejection
def test_raw_tool_call_syntax_rejected():
    """The exact operator-observed failure: raw tool-call text returned as
    a 'successful' answer MUST be rejected."""
    bad = "query_telemetry_db(FDE_VIEWS.VW_SHIPMENTS, TOP 100 * WHERE ShipmentId = 'UC-003916')"
    assert RAW_TOOL_CALL_RE.search(bad)
    v = validate_shipment_answer(bad, "UC-003916")
    assert not v["passed"]
    assert "no raw tool-call syntax" in v["failures"]


def test_raw_sql_syntax_rejected():
    bad = "SELECT TOP 100 * FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'UC-003916'"
    assert RAW_TOOL_CALL_RE.search(bad)


def test_good_shipment_answer_passes():
    good = (
        "## Shipment Report — UC-003916\n\n"
        "This is a **synthetic test data** shipment record.\n\n"
        "| Field | Value |\n|---|---|\n"
        "| Shipment ID | UC-003916 |\n"
        "| Cargo Type | Fresh produce |\n"
        "| Vehicle | VEH-00123 |\n"
        "| Departed | 2021-03-15 |\n"
        "| Arrived | 2021-03-18 |\n"
        "| Verdict | Accepted |\n"
        "| Temperature Range | 0.0 – 4.0 °C |\n\n"
        "Source: FDE_VIEWS (read-only SQL Server view, synthetic test dataset)."
    )
    v = validate_shipment_answer(good, "UC-003916")
    assert v["passed"], f"failures: {v['failures']}"


def test_validator_rejects_wrong_shipment_id():
    v = validate_shipment_answer("Report about UC-000001 (synthetic test data)", "UC-003916")
    assert not v["passed"]
    assert "correct shipment id" in v["failures"]


def test_validator_rejects_missing_synthetic_label():
    v = validate_shipment_answer("UC-003916 arrived on time with cargo.", "UC-003916")
    assert not v["passed"]
    assert "synthetic-data disclosure" in v["failures"]


def test_validator_rejects_credential_leakage():
    leak = "UC-003916 (synthetic data). api_key=nvapi-abc123"
    v = validate_shipment_answer(leak, "UC-003916")
    assert not v["passed"]
    assert "no credential leakage" in v["failures"]


# ---------------------------------------------- gateway routing (2026-11-21 M3:
# validator dispatch is INTENT-based, never answer-text-id-based)
def test_gateway_validates_by_intent_not_answer_text():
    """The M3 fix: api/main.py dispatches validators from the resolved
    request intent; the strict Phase 6 validator stays scoped to the
    UC-003241 demo case via an explicit constant."""
    import inspect
    import api.main as main
    source = inspect.getsource(main)
    assert "validate_shipment_answer" in source
    assert "STRICT_VALIDATOR_SHIPMENT" in source
    assert "UC-003241" in source  # the strict incident validator still covers the demo case
    assert "_validate_answer" in source  # intent-based dispatch helper
    assert 're.search(r"UC-' not in source  # no answer-text id extraction for dispatch


# ---------------------------------------------- no arbitrary SQL concatenation
def test_no_user_text_concatenated_into_sql():
    """All prefetch SQL uses FIXED, guard-valid queries with the shipment
    ID as a parameter — never user text. The format() only substitutes the
    validated UC-xxxxxx pattern."""
    import inspect
    source = inspect.getsource(ep)
    # no f-string SQL construction (format() with validated sid only)
    assert "format(sid=sid)" in source
    # the sid comes from the UC-regex extraction, never user free text
    assert "SHIPMENT_ID_RE" in source
    assert r"\bUC-\d{6}\b" in source
