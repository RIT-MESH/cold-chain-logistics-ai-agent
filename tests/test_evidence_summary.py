"""Deterministic evidence-only fallback tests (operator directive 2026-11-21
§8): the fallback is honestly labeled, evidence-grounded (mocked tool rows),
validator-clean, refuses to invent risk causes or geography, and returns
None on insufficient evidence."""
from unittest.mock import MagicMock, patch

import pytest

import src.evidence_summary as es
from src.answer_validator import (
    validate_answer, validate_comparison_answer, validate_history_answer,
    validate_shipment_answer,
)

# --- canned tool outputs mirroring the REAL dataset rows (UC-003241 has the
# single recorded low-side excursion at 1.2 C; UC-003916 has none).
SHIPMENT_ROW_3241 = ("COLUMNS: shipment_id, cargo_type, vehicle_id, departed_at, "
                     "expected_arrival_at, arrived_at, receiver_verdict, temperature_min_c, "
                     "temperature_max_c, pallets, source_category, synthetic_test_data\n"
                     "('UC-003241', 'Insulin pens', 'VEH-00123', '2026-07-06 14:00:00', "
                     "'2026-07-07 01:00:00', '2026-07-07 00:52:00', 'accepted', "
                     "Decimal('2.0'), Decimal('8.0'), 8, 'historical', 1)")
SHIPMENT_ROW_3916 = ("COLUMNS: shipment_id, cargo_type, vehicle_id, departed_at, "
                     "expected_arrival_at, arrived_at, receiver_verdict, temperature_min_c, "
                     "temperature_max_c, pallets, source_category, synthetic_test_data\n"
                     "('UC-003916', 'Packed salads', 'VEH-0158', '2026-10-31 12:33:23', "
                     "'2026-11-01 00:58:59', '2026-10-31 23:59:59', 'accepted', "
                     "Decimal('1.0'), Decimal('6.0'), 15, 'historical', 1)")
STATS_ROW = ("COLUMNS: n_readings, min_c, max_c, avg_c, first_at, last_at\n"
             "(20, Decimal('1.2'), Decimal('6.2'), Decimal('4.1'), "
             "'2026-07-06 17:15:00', '2026-07-06 23:45:09')")
INCIDENT_ROW = ("COLUMNS: IncidentId, EventId, ShipmentId, ObservedAtUtc, TemperatureC, "
                "ThresholdC, Provenance, Status, CreatedAtUtc\n"
                "('temp:8df143db-78a9-41f9-aafe-c2ad0046906e', "
                "'8df143db-78a9-41f9-aafe-c2ad0046906e', 'UC-003241', '2026-07-06 23:45:09', "
                "Decimal('6.2'), Decimal('4.0'), 'synthetic', 'OPEN', '2026-07-06 23:45:30')")
NO_ROWS = "No records matched the query criteria."
NOTIF_ROW = ("COLUMNS: IncidentId, NotificationState, AttemptCount, AckedCount, "
             "LastAttemptAtUtc\n"
             "('temp:8df143db-78a9-41f9-aafe-c2ad0046906e', 'sns_acked', 1, 1, "
             "'2026-07-06 23:46:00')")
CUSTODY_ROW = ("COLUMNS: handoff_no, received_by, handed_at, seal_intact\n"
               "(1, 'line-haul carrier', '2026-07-06 15:25:02', True)")
TELEMETRY_ROW = ("COLUMNS: recorded_at, sensor_id, temperature_c, humidity_pct, latitude, "
                 "longitude, speed_kmh, fuel_level_pct, risk_level\n"
                 "('2026-07-06 23:45:09', 'S-001', Decimal('6.2'), Decimal('51.0'), "
                 "Decimal('34.9'), Decimal('135.3'), 62.0, Decimal('47.7'), 'high')")
SOP_TEXT = ("--- SOP GUIDANCE (retrieved reference material) ---\n"
            "[Temperature Management | version 1.0-demo | section A3 | source "
            "synthetic-demo | status synthetic-test] (SYNTHETIC TEST DOCUMENT)\n"
            "Verify the auxiliary cooling unit with the driver; divert to cold storage "
            "if the delay exceeds one hour.")


def _fake_query(sql):
    s = sql.upper()
    if "VW_SHIPMENTS" in s:
        return SHIPMENT_ROW_3916 if "UC-003916" in s else SHIPMENT_ROW_3241
    if "COUNT(*)" in s:
        return STATS_ROW
    if "VW_TELEMETRY_INCIDENTS" in s:
        return INCIDENT_ROW if "UC-003241" in s else NO_ROWS
    if "VW_INCIDENT_NOTIFICATIONS" in s:
        return NOTIF_ROW if "UC-003241" in s else NO_ROWS
    if "VW_CUSTODY" in s:
        return CUSTODY_ROW
    if "VW_TELEMETRY" in s and "COUNT" not in s:
        return TELEMETRY_ROW
    return NO_ROWS


def _patch_sop(return_text):
    # _sop_block imports the tool from src.agent_tools at call time, so the
    # MODULE attribute is the patch point (StructuredTool is a pydantic model
    # and cannot be instance-patched).
    mock = MagicMock()
    mock.invoke.return_value = return_text
    return patch("src.agent_tools.search_compliance_sop", mock)


# ------------------------------------------------------------ honest labeling
def test_fallback_is_labeled_not_ai():
    with patch.object(es, "_run_sql", side_effect=lambda sql: es._parse_tool_output(_fake_query(sql))), \
         _patch_sop(SOP_TEXT):
        out = es.deterministic_answer(["UC-003916"], "shipment_report", "report for UC-003916")
    assert "deterministic evidence summary" in out.lower()
    assert "without AI narration" in out
    assert "synthetic" in out.lower()


# ------------------------------------------------------------ report path
def test_fallback_report_passes_shipment_validator():
    with patch.object(es, "_run_sql", side_effect=lambda sql: es._parse_tool_output(_fake_query(sql))), \
         _patch_sop(SOP_TEXT):
        out = es.deterministic_answer(["UC-003916"], "shipment_report", "report for UC-003916")
    v = validate_shipment_answer(out, "UC-003916")
    assert v["passed"], f"failures: {v['failures']}"
    # §6: no invented risk cause; flags reported as recorded
    assert "high" in out and "not store how they were derived" in out
    # §5: coordinates without place labels
    assert "135.3" in out and "California" not in out


# ------------------------------------------------------------ incident_sop path
def test_fallback_incident_sop_passes_strict_validator_with_sop():
    with patch.object(es, "_run_sql", side_effect=lambda sql: es._parse_tool_output(_fake_query(sql))), \
         _patch_sop(SOP_TEXT):
        out = es.deterministic_answer(
            ["UC-003241"], "incident_sop",
            "what happened with UC-003241 and what does the SOP say to do?")
    v = validate_answer(out, sop_available=True)
    assert v["passed"], f"failures: {v['failures']}"


def test_fallback_incident_sop_honest_when_sop_unavailable():
    with patch.object(es, "_run_sql", side_effect=lambda sql: es._parse_tool_output(_fake_query(sql))), \
         _patch_sop("SOP retrieval is currently unavailable (APITimeoutError); answer from SQL evidence."):
        out = es.deterministic_answer(
            ["UC-003241"], "incident_sop",
            "what happened with UC-003241 and what does the SOP say to do?")
    v = validate_answer(out, sop_available=False)
    assert v["passed"], f"failures: {v['failures']}"
    assert "could not be retrieved" in out


# ------------------------------------------------------------ history + comparison
def test_fallback_temperature_history_passes_history_validator():
    with patch.object(es, "_run_sql", side_effect=lambda sql: es._parse_tool_output(_fake_query(sql))), \
         _patch_sop(SOP_TEXT):
        out = es.deterministic_answer(["UC-003916"], "temperature_history",
                                      "what was its highest temperature?")
    v = validate_history_answer(out, "UC-003916")
    assert v["passed"], f"failures: {v['failures']}"
    assert "6.2" in out or "TELEMETRY" in out  # rows rendered


def test_fallback_comparison_passes_comparison_validator():
    with patch.object(es, "_run_sql", side_effect=lambda sql: es._parse_tool_output(_fake_query(sql))), \
         _patch_sop(SOP_TEXT):
        out = es.deterministic_answer(["UC-003916", "UC-003241"], "comparison",
                                      "compare UC-003916 and UC-003241")
    v = validate_comparison_answer(out, "UC-003916", "UC-003241")
    assert v["passed"], f"failures: {v['failures']}"
    # stated-criteria conclusion: 003241 has the incident, 003916 has none
    assert "UC-003916 has fewer confirmed issues" in out


# ------------------------------------------------------------ insufficient evidence
def test_fallback_returns_none_when_shipment_record_missing():
    with patch.object(es, "_run_sql", side_effect=lambda sql: es._parse_tool_output(NO_ROWS)):
        out = es.deterministic_answer(["UC-999999"], "shipment_report", "report for UC-999999")
    assert out is None


def test_parse_tool_output_prettifies_pyodbc_datetimes():
    raw = ("COLUMNS: departed_at, temperature_c\n"
           "(datetime.datetime(2026, 10, 31, 12, 33, 23), Decimal('1.17'))")
    cols, rows = es._parse_tool_output(raw)
    assert cols == ["departed_at", "temperature_c"]
    assert rows == [("2026-10-31 12:33:23", "1.17")]


def test_fallback_no_place_labels_ever():
    with patch.object(es, "_run_sql", side_effect=lambda sql: es._parse_tool_output(_fake_query(sql))), \
         _patch_sop(SOP_TEXT):
        out = es.deterministic_answer(["UC-003916"], "shipment_report", "report for UC-003916")
    # §5: coordinates are reported with the dataset caveat, never a place label
    assert "California" not in out and "Japan" not in out and "Osaka" not in out
    assert "34.9" in out and "135.3" in out  # raw coordinates preserved as recorded
