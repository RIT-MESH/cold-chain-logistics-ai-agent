"""Offline evaluation (Phase 6 directive §4): the synthesis-only answer
validator against RECORDED/mocked answers — no live model calls.

The mocked answers encode (a) the historical GLM passing form (Phase 5
E2E answer shape), (b) the historical Nemotron failure modes (humidity
misread as temperature; fabricated 9.2C; invented procedures; missing
citation), and (c) notification-semantics confusion. The validator must
pass the good form and FAIL every fabrication mode — this is the gate any
synthesis-only model candidate must clear before approval.
"""
import pytest

from src.answer_validator import EVIDENCE, validate_answer

GOOD_ANSWER = f"""
## Incident Evidence — Shipment {EVIDENCE['shipment_id']}
Incident `{EVIDENCE['incident_id']}`: observed **6.2 °C** against the configured
**4.0 °C** threshold (derived severity: +2.2 °C above threshold — severity is
derived, not stored). Status **{EVIDENCE['status']}**, provenance **synthetic**
(separately ingested replay/demo event, not a historical measurement).
Notification state `sns_acked`: the SNS **topic accepted** the notification —
this is NOT confirmation of delivery to any recipient (1 attempt, 1 ack).

## Relevant SOP
[Standard Operating Procedure (SYNTHETIC): Temperature Management | version 1.0-demo |
section A3 | source synthetic-demo | status synthetic-demo] — **SYNTHETIC TEST
DOCUMENT, not an approved operational policy.**
A3 steps: verify the reading/sensor; contact the driver to restart/verify the
auxiliary cooling unit; if correction delay exceeds 1 hour, divert to the
nearest cold-storage facility; document every action.

## Recommended Action
Follow SOP §A3: verify the sensor, have the driver restart the cooling unit,
and divert if the delay exceeds 1 hour. Human review required before action.
"""

NEMOTRON_HUMIDITY_MISREAD = "The excursion shows 81.0% humidity as temperature 9.2 C above the threshold." \
    " Contact maintenance per SOP."  # historical failure: humidity read as temperature + fabricated magnitude

NEMOTRON_FABRICATED_PROCEDURE = "Incident for UC-003241 at 6.2C. Per SOP section Z9, " \
    "quarantine the vehicle and call the port authority immediately."

NEMOTRON_DELIVERY_CONFUSION = "Incident temp:8df143db at 6.2C vs 4.0C threshold, status OPEN. " \
    "The notification was successfully delivered to the recipient's email."

GLM_PASSING_FORM = """Temperature-excursion incident temp:8df143db for UC-003241: 6.2 °C vs 4.0 °C threshold,
derived severity, status OPEN, synthetic provenance. NotificationState sns_acked — topic
acceptance only, not email delivery. SOP Temperature Management v1.0-demo section A3
(synthetic test document): verify sensor, restart cooling via driver, divert if >1 hour.
Human review required."""


def test_good_answer_passes_all_checks():
    result = validate_answer(GOOD_ANSWER)
    assert result["passed"], f"failures: {result['failures']}"
    assert all(ok for _, ok in result["checks"])


def test_historical_glm_form_passes():
    result = validate_answer(GLM_PASSING_FORM)
    assert result["passed"], f"failures: {result['failures']}"


def test_humidity_misread_fails():
    result = validate_answer(NEMOTRON_HUMIDITY_MISREAD)
    assert not result["passed"] and "threshold 4.0C" in result["failures"]


def test_fabricated_procedure_fails():
    result = validate_answer(NEMOTRON_FABRICATED_PROCEDURE)
    assert not result["passed"]
    assert "SOP section A3 cited" in result["failures"] or "no invented SOP section" in result["failures"]


def test_delivery_confusion_fails():
    result = validate_answer(NEMOTRON_DELIVERY_CONFUSION)
    assert not result["passed"] and "SOP section A3 cited" in result["failures"]


def test_no_credential_leakage_check():
    leak = GOOD_ANSWER + " api_key=nvapi-abc123 "
    assert not validate_answer(leak)["passed"]


def test_evidence_package_matches_live_records():
    """The fixed evidence package must equal the VERIFIED live records
    (Phase 4 E2E + this request's audit-verified retrieval) — no drift."""
    assert EVIDENCE["temperature_c"] == "6.2" and EVIDENCE["threshold_c"] == "4.0"
    assert EVIDENCE["incident_id"].startswith("temp:8df143db")
    assert EVIDENCE["notification_state"] == "sns_acked"
    assert EVIDENCE["sop_section"] == "A3" and EVIDENCE["sop_version"] == "1.0-demo"
