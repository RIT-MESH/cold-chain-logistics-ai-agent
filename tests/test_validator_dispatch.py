"""Validator-dispatch regression tests (operator directive 2026-11-21 §2-M3,
§5, §10): validators are selected by request INTENT, never by the shipment
id that happens to appear in the answer. The exact live failure reproduced
2026-11-21: a temperature-history answer about UC-003241 was rejected by the
strict incident+SOP validator (failed 'derived severity consistent', 'SOP
section A3 cited', ...) and returned as a 502."""
from unittest.mock import patch

import api.main as main
from src.answer_validator import (
    geo_label_violation, validate_answer, validate_comparison_answer,
    validate_history_answer, validate_shipment_answer,
)
from src.context_resolver import resolve_conversation
from src.evidence_prefetch import build_evidence_for


class _Res:
    def __init__(self, ids, intent):
        self.ids = list(ids)
        self.intent = intent

    @property
    def active_id(self):
        return self.ids[0] if self.ids else None


# ------------------------------------------------------------ THE M3 regression
def test_temperature_history_answer_mentioning_uc_003241_passes():
    """The exact false-rejection: history answer judged on incident+SOP
    criteria it was never asked about."""
    answer = (
        "Temperature history for shipment UC-003241 (synthetic test data): "
        "20 readings between 2026-07-06 17:15 and 23:45; observed range "
        "1.2 to 6.2 °C against the stated 2.0-8.0 °C envelope, with one "
        "reading (1.2 °C at 23:45:09) below the configured minimum. "
        "Requires human review."
    )
    verdict = main._validate_answer(answer, _Res(["UC-003241"], "temperature_history"),
                                    sop_available=False)
    assert verdict["passed"], f"failures: {verdict['failures']}"


def test_shipment_report_answer_mentioning_uc_003241_passes():
    answer = ("Shipment UC-003241 (synthetic test data) carried insulin pens and was "
              "accepted by the receiver. Requires human review.")
    verdict = main._validate_answer(answer, _Res(["UC-003241"], "shipment_report"),
                                    sop_available=False)
    assert verdict["passed"], f"failures: {verdict['failures']}"


def test_strict_validator_still_applies_to_genuine_incident_sop_intent():
    # Only the UC-003241 demo case with incident+SOP intent is strict.
    good = ("Shipment UC-003241 incident temp:8df143db-78a9-41f9-aafe-c2ad0046906e is OPEN: "
            "6.2 °C observed against the 4.0 °C threshold (derived severity 2.2). "
            "Notification state sns_acked — the SNS topic accepted the message; this does NOT confirm recipient delivery. "
            "Per [Temperature Management | version 1.0-demo | section A3 | source synthetic-demo | "
            "status synthetic-test], corrective action includes verifying cooling with the driver "
            "or diverting. All data is synthetic and requires human review.")
    verdict = main._validate_answer(good, _Res(["UC-003241"], "incident_sop"),
                                    sop_available=True)
    assert verdict["passed"], f"failures: {verdict['failures']}"


def test_incident_sop_for_other_shipment_uses_light_validator():
    # The strict validator is evidence-bound to UC-003241; another shipment's
    # incident+SOP answer must not be judged on the UC-003241 ground truth.
    answer = ("Shipment UC-003916 (synthetic test data): no incidents recorded; "
              "SOP guidance could not be retrieved. Human review required.")
    verdict = main._validate_answer(answer, _Res(["UC-003916"], "incident_sop"),
                                    sop_available=False)
    assert verdict["passed"], f"failures: {verdict['failures']}"


# ------------------------------------------------------------ honest SOP absence (§9)
def test_strict_validator_accepts_honest_sop_unavailability():
    answer = ("Shipment UC-003241 incident temp:8df143db-78a9-41f9-aafe-c2ad0046906e is OPEN: "
              "6.2 °C observed against the 4.0 °C threshold (derived severity 2.2). "
              "Notification state sns_acked — the SNS topic accepted the message; this does NOT confirm recipient delivery. "
              "SOP guidance could not be retrieved at this time, so no procedure is cited. "
              "All data is synthetic and requires human review.")
    verdict = validate_answer(answer, sop_available=False)
    assert verdict["passed"], f"failures: {verdict['failures']}"


def test_strict_validator_rejects_fabricated_citation_when_sop_unavailable():
    answer = ("Shipment UC-003241 incident temp:8df143db-78a9-41f9-aafe-c2ad0046906e is OPEN: "
              "6.2 °C against the 4.0 °C threshold (derived severity 2.2), sns_acked — "
              "NOT delivered. Per [Temperature Management | version 1.0-demo | section A3 | "
              "source synthetic-demo | status synthetic], verify cooling. Synthetic data, "
              "human review required.")
    verdict = validate_answer(answer, sop_available=False)
    assert not verdict["passed"]
    assert "no fabricated SOP citation when SOP unavailable" in verdict["failures"]


# ------------------------------------------------------------ geography (§5)
def test_geo_label_violation_detected():
    bad = ("GPS track remained in the California region "
           "(lat approximately 34.9, lon approximately 135.3).")
    assert geo_label_violation(bad) is True


def test_plain_coordinates_pass():
    ok = "The latest recorded reading was at lat 34.9, lon 135.3 (synthetic GPS values)."
    assert geo_label_violation(ok) is False


def test_region_word_away_from_coordinates_passes():
    ok = ("Southern California Logistics Operations requires review. "
          "The latest reading was at lat 34.9, lon 135.3.")
    assert geo_label_violation(ok) is False


def test_shipment_validator_rejects_geo_label():
    bad = ("Shipment UC-003916 (synthetic data) stayed in the California region "
           "(lat approximately 34.9, lon approximately 135.3).")
    v = validate_shipment_answer(bad, "UC-003916")
    assert not v["passed"]
    assert "no unsupported geographical label" in v["failures"]


# ------------------------------------------------------------ comparison validator
def test_comparison_answer_passes():
    answer = ("Comparison of UC-003916 and UC-003241 (synthetic test data): "
              "UC-003916 has no recorded incidents; UC-003241 has one recorded "
              "temperature-excursion incident. On the stated criterion of recorded "
              "incidents, UC-003916 shows fewer confirmed issues. Human review required.")
    v = validate_comparison_answer(answer, "UC-003916", "UC-003241")
    assert v["passed"], f"failures: {v['failures']}"


def test_comparison_answer_missing_second_id_rejected():
    v = validate_comparison_answer("UC-003916 only (synthetic).", "UC-003916", "UC-003241")
    assert not v["passed"]
    assert "both shipment ids present" in v["failures"]


# ------------------------------------------------------------ evidence markers
def test_evidence_block_carries_sop_status_marker(monkeypatch):
    monkeypatch.setenv("ENABLE_EVIDENCE_PREFETCH", "true")
    with patch("src.evidence_prefetch._invoke_tool") as fake:
        def invoke(tool, payload):
            text = str(payload.get("sql_query", payload.get("query", "")))
            if "VW_" in text:
                return "COLUMNS: x\n('row',)"
            return "SOP retrieval is currently unavailable (APITimeoutError); answer from SQL evidence."
        fake.side_effect = invoke
        evidence = build_evidence_for(["UC-003241"], "incident_sop",
                                       "what corrective action does the SOP recommend")
    assert "SOP STATUS: unavailable" in evidence
    assert "never invent citations" in evidence.lower()


def test_intent_from_real_conversation_drives_dispatch():
    """End-to-end-ish: resolve the operator's actual drill conversation and
    confirm each follow-up lands on the right validator path."""
    from langchain_core.messages import AIMessage, HumanMessage
    msgs = [HumanMessage(content="hi"), AIMessage(content="hello"),
            HumanMessage(content="UC-003916 report"),
            AIMessage(content="Shipment UC-003916 (synthetic): accepted, no incidents."),
            HumanMessage(content="any problem in this shipment?")]
    r = resolve_conversation(msgs)
    assert r.ids == ["UC-003916"] and r.intent == "incident"
    # dispatch falls to the light shipment validator for 'incident' intent
    verdict = main._validate_answer(
        "Shipment UC-003916 (synthetic test data): no recorded incidents; some "
        "readings carry high risk classifications. Human review required.",
        r, sop_available=False)
    assert verdict["passed"], f"failures: {verdict['failures']}"


def test_gateway_dispatch_is_intent_based_in_source():
    """M3 structural assertion: api/main.py must NOT dispatch validators by
    searching the ANSWER for a shipment id."""
    import inspect
    src = inspect.getsource(main)
    assert "_validate_answer" in src
    assert "STRICT_VALIDATOR_SHIPMENT" in src  # strict validator scoped to the demo case
    assert 're.search(r"UC-' not in src  # no answer-text id extraction for dispatch
