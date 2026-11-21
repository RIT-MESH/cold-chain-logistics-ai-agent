"""Unit tests for the final-validation accuracy checkers and the
production-shaped SQL executor (architecture-review/nim_validation_common).

These encode the specific error modes observed in the earlier benchmarks:
- humidity 81.0 reported as a temperature (nemotron-lightning failure)
- fabricated high-side excursions 9.2 / 8.9 °C (old fixture-defect answers)
- fluent "no temperature excursion" claims that miss the known 1.2 °C excursion
- fabricated arrival/delay data (not exposed by any approved view)
- completeness overclaims about 10-row sampled telemetry

All offline: no API calls, no network. The executor tests run REAL guard
rejections and REAL SQLite executions against the synthetic dataset.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "architecture-review"))

from nim_validation_common import check_answer, classify_outcome, execute_query, GROUND_TRUTH


# ---------------------------------------------------------------- executor --
def test_executor_blocks_limit_syntax():
    """bolt's LIMIT dialect: the lenient guard ACCEPTS it (sqlglot maps LIMIT
    onto TOP's node), but production SQL Server execution would fail — the
    executor must mirror that execution failure, never run it on SQLite."""
    out, outcome = execute_query(
        "SELECT cargo_type FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'UC-003241' LIMIT 1")
    assert outcome == "error"  # execution failure, exactly as production
    assert "Database query failed" in out


def test_executor_blocks_top_less_queries():
    out, outcome = execute_query(
        "SELECT cargo_type FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'UC-003241'")
    assert outcome == "blocked"
    assert "TOP" in out  # the actual production error reaches the agent


def test_executor_blocks_unqualified_view_names():
    """All models in the earlier comparison queried bare view names — the
    production guard requires the FDE_VIEWS schema prefix."""
    out, outcome = execute_query(
        "SELECT TOP 5 cargo_type FROM VW_SHIPMENTS WHERE shipment_id = 'UC-003241'")
    assert outcome == "blocked"
    assert "FDE_VIEWS" in out


def test_executor_blocks_mutations():
    out, outcome = execute_query("DELETE FROM FDE_VIEWS.VW_SHIPMENTS")
    assert outcome == "blocked"


def test_executor_executes_real_data():
    out, outcome = execute_query(
        "SELECT TOP 5 recorded_at, temperature_c FROM FDE_VIEWS.VW_TELEMETRY "
        "WHERE shipment_id = 'UC-003241' ORDER BY recorded_at")
    assert isinstance(outcome, dict) and outcome["ok"] == 5
    assert "6.3" in out  # real first reading from the dataset


def test_executor_finds_real_excursion():
    out, outcome = execute_query(
        "SELECT TOP 10 recorded_at, temperature_c FROM FDE_VIEWS.VW_TELEMETRY "
        "WHERE shipment_id = 'UC-003241' AND (temperature_c < 2.0 OR temperature_c > 8.0)")
    assert isinstance(outcome, dict) and outcome["ok"] >= 1
    assert "1.2" in out and "23:45" in out


def test_executor_high_side_filter_is_truthfully_empty():
    out, outcome = execute_query(
        "SELECT TOP 10 temperature_c FROM FDE_VIEWS.VW_TELEMETRY "
        "WHERE shipment_id = 'UC-003241' AND temperature_c > 8.0")
    assert outcome == "empty"


def test_executor_column_allowlist():
    """Columns outside the REAL production view definitions (per
    scripts/setup_unified_views.sql) must fail like production SQL Server —
    and the real view columns, including VW_SHIPMENTS' arrival times that the
    old tool docstring omitted, must execute against the dataset."""
    out, outcome = execute_query(
        "SELECT TOP 5 expected_arrival_at, arrived_at FROM FDE_VIEWS.VW_SHIPMENTS "
        "WHERE shipment_id = 'UC-003241'")
    assert isinstance(outcome, dict) and outcome["ok"] == 1
    assert "01:45" in out  # real arrival data (repair discovery: delay evidence IS queryable)
    out, outcome = execute_query(
        "SELECT TOP 5 driver_name FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id = 'UC-003241'")
    assert outcome == "column_error"


def test_executor_row_cap():
    out, outcome = execute_query(
        "SELECT TOP 100 recorded_at FROM FDE_VIEWS.VW_TELEMETRY WHERE shipment_id = 'UC-003241' "
        "ORDER BY recorded_at")
    assert outcome["ok"] == 10 and outcome["truncated"] is True  # 16 exist, tool caps at 10


# ----------------------------------------------------------------- checkers --
CTX = {"telemetry_sampled": True, "weather_temp": None, "sql_evidence": True}


def test_humidity_as_temperature_is_rejected():
    answer = "The shipment had readings of 81.0 °C at 17:45 — a severe breach."
    res = check_answer("excursion", answer, CTX)
    assert not res["pass"]
    assert res["temperature_violations"][0]["kind"] == "humidity_confusion"


def test_fabricated_high_excursion_is_rejected():
    answer = "Peak of 9.2 °C was recorded above the 8.0 °C limit."
    res = check_answer("excursion", answer, CTX)
    assert not res["pass"]
    assert res["temperature_violations"][0]["kind"] == "fabricated_temperature"


def test_missing_known_excursion_is_rejected():
    answer = ("All recorded readings stayed within the shipment's configured range. "
              "No temperature excursion was observed. Values like 6.3 °C and 7.8 °C were normal.")
    res = check_answer("excursion", answer, CTX)
    assert not res["pass"]
    assert res["checks"]["does_not_miss_known_excursion"] is False
    assert res["checks"]["no_completeness_overclaim"] is False


def test_correct_excursion_answer_passes():
    answer = ("Yes — one low-side excursion: 1.2 °C at 2026-07-06 23:45:09, below the 2.0 °C minimum. "
              "Other sampled readings (6.3 °C, 7.8 °C) were in range; this is a 10-row sample of the trip.")
    res = check_answer("excursion", answer, CTX)
    assert res["pass"], res


def test_completeness_claim_with_gap_acknowledgment_passes():
    answer = "The available sample covers the window; other readings may not be included."
    _, res_comp = None, check_answer("excursion", answer, CTX)
    assert res_comp["checks"]["no_completeness_overclaim"] is True


def test_delay_correct_claims_pass():
    """Delay evidence IS exposed by VW_SHIPMENTS (expected_arrival_at/
    arrived_at); correct claims (arrived 01:45, 8 minutes late) must pass."""
    answer = ("Shipment UC-003241 arrived at 01:45, delayed by 8 minutes versus the "
              "01:37 expectation (VW_SHIPMENTS expected_arrival_at/arrived_at).")
    res = check_answer("delay", answer, CTX)
    assert res["pass"], res
    assert res["checks"]["delay_claims_match_records"] is True
    assert res["checks"]["arrival_claims_match_records"] is True


def test_delay_wrong_claims_are_rejected():
    """Invented delay durations or arrival times contradict the database."""
    answer = ("Shipment UC-003241 arrived at 02:30, delayed by 30 minutes due to a "
              "border hold that the database does not actually contain.")
    res = check_answer("delay", answer, CTX)
    assert not res["pass"]
    assert res["checks"]["delay_claims_match_records"] is False
    assert res["checks"]["arrival_claims_match_records"] is False


def test_safe_delay_refusal_is_classified_not_incorrect():
    """A model that could not retrieve delay data and says so honestly is a
    safe refusal — not incorrect, and not a completed investigation."""
    answer = ("The required arrival records could not be retrieved, so I cannot "
              "determine delays for this shipment from the available evidence.")
    res = check_answer("delay", answer, CTX)
    outcome = classify_outcome({"status": "ok", "acceptance": res, "answer_excerpt": answer})
    assert outcome == "safe_refusal"


def test_sop_wrong_band_claim_is_rejected():
    answer = ("Shipment UC-003241 must stay in the allowed range of 0.0 to 4.0 per the SOP "
              "for fresh perishables; auxiliary cooling is the mitigation.")
    res = check_answer("sop", answer, CTX)
    assert not res["pass"]
    assert res["checks"]["no_wrong_band_for_shipment"] is False


def test_lookup_pass_case():
    answer = ("Shipment UC-003241: insulin pens, allowed range 2.0 °C to 8.0 °C, "
              "receiver verdict accepted (from VW_SHIPMENTS).")
    res = check_answer("lookup", answer, CTX)
    assert res["pass"], res


def test_sop_threshold_citations_anywhere_are_not_fabrication():
    """Discovered via the battery: the SOP tool is callable in every scenario,
    so citing its 0.0–4.0 °C thresholds in a delay/weather answer is not a
    fabrication (previous checker version falsely flagged these)."""
    answer = ("Shipment UC-003241: delay data is not available in the approved views. "
              "For context, the SOP's fresh-perishables threshold is 0.0°C to 4.0°C, "
              "which does not apply to this cargo.")
    res = check_answer("delay", answer, CTX)
    assert res["pass"], res
    assert res["checks"]["no_fabricated_or_confused_temperatures"] is True


def test_ground_truth_shape():
    assert GROUND_TRUTH["telemetry_count"] == 16
    assert 1.2 in GROUND_TRUTH["temperatures"]
    assert 9.2 not in GROUND_TRUTH["temperatures"]
    assert 81.0 in GROUND_TRUTH["humidities"]


# --------------------------------------- post-repair qualification §3 ------
def test_correct_answer_without_tool_execution_is_not_complete():
    """Qualification rule: successful tool execution is REQUIRED for a
    complete verdict — a factually correct answer with no SQL evidence
    behind it cannot pass as completed."""
    answer = ("Shipment UC-003241: insulin pens, allowed range 2.0 °C to 8.0 °C, "
              "receiver verdict accepted.")
    res = check_answer("lookup", answer, {"telemetry_sampled": False, "sql_evidence": False})
    assert not res["pass"]
    assert res["checks"]["successful_tool_execution"] is False


def test_fabricated_humidity_is_rejected():
    """Cited humidity values must match real records (lightning's repaired
    run cited the real 69 % at excursion time; a made-up value fails)."""
    answer = ("Shipment UC-003241: one excursion at 23:45 (1.2 °C, below the 2.0 °C minimum); "
              "humidity at that time was 95 %.")
    res = check_answer("excursion", answer, CTX)
    assert not res["pass"]
    assert res["humidity_violations"][0]["kind"] == "fabricated_humidity"


def test_fuel_percentage_near_humidity_is_not_a_humidity_claim():
    """Qualification finding: real fuel_level_pct values (94.54 first row,
    47.66 last row) cited near humidity mentions were falsely flagged by the
    window heuristic; the nearest-preceding-label rule fixes it."""
    answer = ("Shipment UC-003241: full range 1.2–7.8 °C; humidity 69–84%; "
              "fuel declined 94.54% → 47.66%; final row: humidity 80%, "
              "congestion 5.03, fuel 47.66%.")
    res = check_answer("excursion", answer, CTX)
    assert res["humidity_violations"] == [], res["humidity_violations"]
    assert res["checks"]["no_fabricated_or_confused_temperatures"] is True


def test_correct_humidity_citation_passes():
    answer = ("Shipment UC-003241: one excursion at 23:45 (1.2 °C, below the 2.0 °C minimum); "
              "humidity at that time was 69 %; other sampled readings were in range.")
    res = check_answer("excursion", answer, CTX)
    assert res["pass"], res
def test_derived_difference_with_direction_is_allowed():
    """"0.2 °C below the limit" is a legitimate derivation (8.0 − 7.8)."""
    answer = "The peak reading was 7.8 °C — 0.2 °C below the 8.0 °C limit; the low excursion hit 1.2 °C at 23:45 (below the 2.0 °C minimum)."
    res = check_answer("excursion", answer, CTX)
    assert res["pass"], res


def test_all_other_readings_scoping_is_correct():
    """Repair-run false positive: 'all OTHER readings remained within' while
    correctly citing the real excursion is correct scoping, not a missed
    excursion (lightning's repaired-interface answer)."""
    answer = ("Excursion detected: 1.2 °C at 2026-07-06 23:45:09, below the 2.0 °C minimum "
              "(alert: medium severity). All other telemetry readings remained within the "
              "2.0 °C to 8.0 °C band.")
    res = check_answer("excursion", answer, CTX)
    assert res["pass"], res
    assert res["checks"]["does_not_miss_known_excursion"] is True


def test_derived_deviation_of_cited_excursion_is_allowed():
    """Repair-run false positive: 'the ~0.8 °C value' (2.0 − 1.2) derived from
    the cited real excursion is legitimate analysis, not fabrication."""
    answer = ("Yes — one low-side excursion: 1.2 °C at 23:45 (below the 2.0 °C minimum); "
              "I could not re-query the view to confirm the ~0.8 °C value, and there are "
              "no other readings outside the band in the 10-row sample.")
    res = check_answer("excursion", answer, CTX)
    assert res["pass"], res
    assert res["checks"]["no_fabricated_or_confused_temperatures"] is True


def test_bare_invented_value_is_fabrication_even_if_a_delta_is_possible():
    """"(0.8 °C)" presented as a candidate excursion reading, with no
    direction/limit context, is a fabrication even though |2.0−1.2| = 0.8
    exists as a possible derived delta (final-gate failure mode: glm
    'whether the deviation was high (9.2°C) or low (0.8°C)')."""
    answer = "I cannot tell whether the deviation was high (9.2 °C) or low (0.8 °C) for UC-003241 at 23:45."
    res = check_answer("excursion", answer, CTX)
    assert not res["pass"]
    kinds = {v["kind"] for v in res["temperature_violations"]}
    assert kinds == {"fabricated_temperature"}


def test_sop_investigation_without_sql_evidence_is_not_completed():
    """Repair commission §3: an SOP investigation without shipment SQL
    evidence must NOT pass as a completed investigation."""
    answer = ("Shipment UC-003241: per the SOP, fresh perishables must stay between "
              "0.0 °C and 4.0 °C; the auxiliary cooling unit is the mitigation.")
    res = check_answer("sop", answer, {"telemetry_sampled": False, "sql_evidence": False})
    assert not res["pass"]
    assert res["checks"]["has_shipment_sql_evidence"] is False


def test_sop_investigation_with_sql_evidence_passes():
    answer = ("Shipment UC-003241 (insulin pens, 2.0 °C to 8.0 °C, accepted) per VW_SHIPMENTS; "
              "the SOP's fresh-perishables threshold (0.0 °C to 4.0 °C) does not apply to this cargo — "
              "for an excursion, restart the auxiliary cooling unit.")
    res = check_answer("sop", answer, {"telemetry_sampled": False, "sql_evidence": True})
    assert res["pass"], res


def test_weather_investigation_without_weather_tool_result_is_not_completed():
    """Repair commission §3: a weather investigation without the required
    weather tool result must NOT pass as completed."""
    answer = ("Shipment UC-003241 route conditions: synthetic test data; weather was probably mild.")
    res = check_answer("weather", answer, {"telemetry_sampled": True, "weather_temp": None})
    assert not res["pass"]
    assert res["checks"]["weather_tool_result_present"] is False


def test_weather_investigation_with_real_weather_result_passes():
    answer = ("Shipment UC-003241 (synthetic test data): current external weather at the last "
              "known position is 12.4 °C (NOT LIVE FLEET TELEMETRY — external weather context only).")
    res = check_answer("weather", answer, {"telemetry_sampled": True, "weather_temp": 12.4,
                                            "sql_evidence": True})
    assert res["pass"], res


# --------------------------------------------------- outcome classes -------
def test_classify_outcome_complete_correct():
    answer = ("Yes — one low-side excursion: 1.2 °C at 23:45, below the 2.0 °C minimum. "
              "Other sampled readings (6.3 °C, 7.8 °C) were in range; this is a 10-row sample of the trip.")
    acc = check_answer("excursion", answer, CTX)
    assert classify_outcome({"status": "ok", "acceptance": acc, "answer_excerpt": answer}) == "complete_correct"


def test_classify_outcome_incorrect_on_fabrication():
    answer = "The peak of 9.2 °C exceeded the 8.0 °C limit."
    acc = check_answer("excursion", answer, CTX)
    assert classify_outcome({"status": "ok", "acceptance": acc, "answer_excerpt": answer}) == "incorrect"


def test_classify_outcome_api_failure():
    rec = {"status": "error", "error": "GraphRecursionError", "acceptance": None, "answer_excerpt": ""}
    assert classify_outcome(rec) == "api_or_recursion_failure"


def test_classify_outcome_safe_refusal_vs_partial():
    refused = ("The required records could not be retrieved, so I cannot determine this "
               "for the shipment from the available evidence.")
    acc = check_answer("excursion", refused, CTX)
    assert classify_outcome({"status": "ok", "acceptance": acc, "answer_excerpt": refused}) == "safe_refusal"
    evasive = "The shipment had a temperature pattern worth reviewing."  # no refusal, no citations
    acc2 = check_answer("excursion", evasive, CTX)
    assert classify_outcome({"status": "ok", "acceptance": acc2, "answer_excerpt": evasive}) == "partial"
