"""Common machinery for NVIDIA NIM model validation (updated 2026-11-09 for
the SQL tool-interface repair).

THE SQL EXECUTION PATH IS PRODUCTION-SHAPED:
  1. The REAL src/sql_guard.validate_query runs first; blocked SQL returns
     the actual security error text — never silently executed.
  2. Guard-passing T-SQL is transpiled (sqlglot) and executed against the
     REAL unified SQLite dataset in read-only mode.
  3. Per-view column allowlists now mirror the REAL FDE_VIEWS definitions
     (scripts/setup_unified_views.sql) — including VW_SHIPMENTS' arrival
     columns, which the old tool docstring omitted.
  4. fetchmany(10) mirrors the production tool's row cap.

ACCURACY CHECKERS distinguish five outcome classes (§3 of the repair
commission):
  complete_correct / partial / safe_refusal / incorrect /
  api_or_recursion_failure
Rules enforced:
  - an SOP investigation without SQL shipment evidence does NOT pass as
    completed;
  - a weather investigation without a real weather tool result does NOT
    pass as completed;
  - numerical values are classified: measured temperatures (VW_TELEMETRY
    readings), configured shipment limits (2.0/8.0), SOP thresholds
    (0.0/4.0), live weather readings, and DERIVED differences (e.g.
    "0.2 °C below the 8.0 limit" from a real reading) — a derived value is
    accepted only with an explicit direction/limit context; bare invented
    values ("9.2 °C") are fabricated;
  - delay claims (duration/arrival times) are verified against the REAL
    shipment record (arrived 01:45:09 vs expected 01:37:09 = 8 minutes).
"""
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.sql_guard import validate_query  # the REAL production guard

import sqlglot
from sqlglot import exp

DB_PATH = ROOT / "data/unified/unified_cold_chain.sqlite"

VIEW_TO_TABLE = {
    "vw_shipments": "shipments",
    "vw_telemetry": "telemetry",
    "vw_custody": "custody_handoffs",
    "vw_alerts": "alerts",
    "vw_sensors": "sensors",
    "vw_vehicles": "vehicles",
    "vw_routes": "route_events",
}
# REAL FDE_VIEWS columns per scripts/setup_unified_views.sql.
VIEW_COLUMNS = {
    "vw_shipments": {"shipment_id", "original_reference", "cargo_type", "pallets", "vehicle_id",
                      "departed_at", "expected_arrival_at", "arrived_at", "receiver_verdict",
                      "origin_lat", "origin_lon", "destination_lat", "destination_lon",
                      "temperature_min_c", "temperature_max_c", "source_category", "synthetic_test_data"},
    "vw_telemetry": {"telemetry_id", "shipment_id", "sensor_id", "vehicle_id", "recorded_at",
                     "latitude", "longitude", "speed_kmh", "temperature_c", "humidity_pct",
                     "traffic_congestion", "fuel_consumption_l_per_100km", "estimated_fuel_used_l",
                     "fuel_level_pct", "risk_level", "source_category", "synthetic_test_data"},
    "vw_custody": {"handoff_id", "shipment_id", "handoff_no", "received_by", "handed_at",
                   "seal_intact", "source_category"},
    "vw_alerts": {"alert_id", "shipment_id", "telemetry_id", "alert_at", "alert_type",
                  "severity", "message", "source_category"},
    "vw_sensors": {"sensor_id", "shipment_id", "serial_no", "position", "installed_at",
                   "device_type", "source_category"},
    "vw_vehicles": {"vehicle_id", "vehicle_type", "capacity_pallets", "fuel_type", "fleet_key"},
    "vw_routes": {"route_event_id", "shipment_id", "telemetry_id", "route_sequence",
                  "recorded_at", "latitude", "longitude", "traffic_congestion", "weather_severity"},
}


class _ColumnError(Exception):
    pass


def load_ground_truth(shipment_id="UC-003241"):
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT recorded_at, temperature_c, humidity_pct FROM telemetry "
                "WHERE shipment_id=? ORDER BY recorded_at", (shipment_id,))
    rows = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT alert_at, alert_type, severity, message FROM alerts WHERE shipment_id=?", (shipment_id,))
    alerts = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT cargo_type, temperature_min_c, temperature_max_c, receiver_verdict, "
                "departed_at, expected_arrival_at, arrived_at FROM shipments WHERE shipment_id=?", (shipment_id,))
    ship = dict(cur.fetchone())
    con.close()
    return {
        "shipment_id": shipment_id,
        "telemetry": rows,
        "temperatures": {round(r["temperature_c"], 1) for r in rows},
        "humidities": {round(r["humidity_pct"], 1) for r in rows},
        "telemetry_count": len(rows),
        "alerts": alerts,
        "ship": ship,
    }


GROUND_TRUTH = load_ground_truth()

# Derived-difference allowance: |a-b| over measured temps + configured limits +
# SOP thresholds. E.g. 8.0-7.8=0.2 ("0.2 °C below the limit") is legitimate.
_BASE = set(GROUND_TRUTH["temperatures"]) | {2.0, 8.0, 0.0, 4.0}
DERIVED_DELTAS = {round(abs(a - b), 1) for a in _BASE for b in _BASE}


def execute_query(query):
    """Production-shaped SQL execution. Returns (result_text, outcome).

    outcome: ok | blocked | empty | column_error | error (dict with row info
    when ok). Repeated/limit violations mirror the production guard exactly.
    """
    try:
        validate_query(query)
    except ValueError as exc:
        return f"SECURITY BLOCK: {exc}", "blocked"
    if re.search(r"\bLIMIT\b", query, re.I):
        # T-SQL has no LIMIT. sqlglot's lenient tsql parser maps LIMIT onto
        # the same node as TOP, so the GUARD accepts it — but a production
        # SQL Server execution would fail. Mirror the execution failure.
        return "Database query failed; inspect service logs.", "error"
    try:
        statement = sqlglot.parse(query, read="tsql")[0]
        view = None
        for t in statement.find_all(exp.Table):
            name = t.name.lower()
            if name in VIEW_TO_TABLE:
                view = name
                t.set("catalog", None)
                t.set("db", None)
                t.set("this", exp.to_identifier(VIEW_TO_TABLE[name]))
        allow = VIEW_COLUMNS.get(view) if view else None
        if allow:
            for c in statement.find_all(exp.Column):
                if c.name.lower() not in allow:
                    raise _ColumnError(c.name)
        sqlite_sql = statement.sql(dialect="sqlite")
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = con.execute(sqlite_sql)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(10)
        truncated = cur.fetchone() is not None
        con.close()
        if not rows:
            return "No records matched the query criteria.", "empty"
        out = f"COLUMNS: {', '.join(columns)}\n"
        for row in rows:
            out += str(tuple(row)) + "\n"
        return out, {"ok": len(rows), "truncated": truncated}
    except _ColumnError:
        return "Database query failed; inspect service logs.", "column_error"
    except Exception:
        return "Database query failed; inspect service logs.", "error"


# ------------------------------------------------------------- checkers ----
_TEMP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:°\s*C\b|degrees celsius|°C\b|\bdeg C\b)")
_HUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_DIRECTION_AFTER = re.compile(r"^\W{0,3}(below|above|under|over|less than|more than|outside|within|"
                              r"from the|of the|short of|beneath|down|up|lower|higher)", re.I)
_GAP_ACK = re.compile(r"sample|not covered|first 10|top 10|subset|available window|"
                      r"may not include|does not (?:include|cover) all|partial|only 10|10 of|"
                      r"truncated|limited (?:to|window)|data (?:gap|shown)", re.I)
_COMPLETENESS = re.compile(r"(all|every)\s+(?:16\s+|recorded\s+|telemetry\s+)?(?:readings?|records?|"
                           r"measurements?|data points?)|throughout (?:the|its) (?:entire|whole|complete|full)|"
                           r"complete (?:record|history|timeline)|entire trip", re.I)
_NO_EXCURSION = re.compile(r"no (?:temperature )?excursion|no(?:ne)? (?:readings?|values?) (?:were )?outside|"
                           r"stayed within (?:the|its) configured|remained (?:within|inside)", re.I)
_UNAVAILABLE = re.compile(r"not (?:available|exposed|provided|included|accessible|retrieved)|unavailable|"
                          r"cannot (?:determine|verify|confirm|retrieve)|insufficient|no (?:delay|arrival|records)|"
                          r"outside (?:the|these) (?:approved|approved views|views)|"
                          r"approved views do not (?:include|expose)|could not be retrieved", re.I)


def check_temperature_values(answer, extra_allowed=(), derived_ok=False):
    """Classify every 'N °C' value: measured reading / configured limit (2.0,
    8.0) / SOP threshold (0.0, 4.0) / injected live weather value / DERIVED
    difference. A derived difference (e.g. 2.0-1.2=0.8) is accepted when
    derived_ok (the answer anchors the real excursion — cites 1.2 °C/23:45)
    or when an explicit direction/limit context follows the value. Bare
    invented candidate values remain violations.
    Anything else is: humidity_confusion (value matches a real humidity) or
    fabricated_temperature."""
    gt = GROUND_TRUTH
    allowed = set(gt["temperatures"]) | {2.0, 8.0, 0.0, 4.0} | set(float(x) for x in extra_allowed)
    violations = []
    for m in _TEMP_RE.finditer(answer):
        val = round(float(m.group(1)), 1)
        if any(abs(val - a) <= 0.05 for a in allowed):
            continue
        if any(abs(val - d) <= 0.05 for d in DERIVED_DELTAS):
            if derived_ok:
                continue
            tail = answer[m.end():m.end() + 25]
            head = answer[max(0, m.start() - 25):m.start()]
            if _DIRECTION_AFTER.search(tail) or re.search(r"~|≈|approx", head[-6:]) or \
               re.search(r"^\W{0,3}(?:value|deviation|difference|delta|margin|gap)", tail, re.I):
                continue
        kind = "humidity_confusion" if any(abs(val - h) <= 0.05 for h in gt["humidities"]) else "fabricated_temperature"
        violations.append({"value": m.group(1), "kind": kind, "context": answer[max(0, m.start() - 30):m.end() + 30]})
    return (not violations), violations


def check_humidity_values(answer):
    """Every percentage whose NEAREST PRECEDING label is humidity must match a
    real humidity reading. Fuel/traffic percentages near humidity mentions
    are NOT humidity claims (qualification finding: real fuel_level_pct
    values 94.54/47.66 were falsely flagged by the window heuristic).
    Returns (pass, violations)."""
    violations = []
    for m in _HUM_RE.finditer(answer):
        head = answer[max(0, m.start() - 60):m.start()].lower()
        positions = {}
        for label in ("humidity", "fuel", "congestion", "traffic", "level"):
            p = head.rfind(label)
            if p >= 0:
                positions[label] = p
        if not positions:
            continue  # unlabeled percentage (table cell etc.) — not a humidity claim
        nearest = max(positions, key=positions.get)
        if nearest == "level":
            nearest = "fuel" if head.rfind("fuel") > head.rfind("humidity") else "humidity"
        if nearest != "humidity":
            continue
        val = round(float(m.group(1)), 1)
        if val in {round(h, 1) for h in (69.0, 71.0, 72.0, 73.0, 74.0, 75.0,
                                          77.0, 79.0, 80.0, 81.0, 84.0)}:
            continue
        violations.append({"value": m.group(1), "kind": "fabricated_humidity",
                           "context": answer[max(0, m.start() - 30):m.end() + 30]})
    return (not violations), violations


def check_completeness_claim(answer, telemetry_sampled=True):
    if not telemetry_sampled:
        return True, None
    hit = _COMPLETENESS.search(answer)
    if hit and not _GAP_ACK.search(answer):
        return False, hit.group(0)
    return True, None


def check_answer(scenario, answer, ctx=None):
    """Objective acceptance checks per scenario against ground truth.
    ctx: {"weather_temp": float|None, "telemetry_sampled": bool,
           "sql_evidence": bool (>=1 successful SQL query in the conversation)}.
    An answer passes only if EVERY check passes.
    """
    ctx = ctx or {}
    low = (answer or "").lower()
    gt = GROUND_TRUTH
    checks = {}

    extras = [0.0, 4.0] + ([ctx["weather_temp"]] if ctx.get("weather_temp") else [])
    temp_ok, temp_viol = check_temperature_values(answer, extras)
    hum_ok, hum_viol = check_humidity_values(answer)
    checks["no_fabricated_or_confused_temperatures"] = temp_ok and hum_ok
    comp_ok, comp_hit = check_completeness_claim(answer, ctx.get("telemetry_sampled", True))
    checks["no_completeness_overclaim"] = comp_ok
    # Post-repair qualification §3: successful tool execution is REQUIRED for
    # a complete verdict on every SQL-based scenario (weather additionally
    # requires the weather-tool result, enforced in its branch below).
    checks["successful_tool_execution"] = bool(ctx.get("sql_evidence"))

    if scenario == "lookup":
        checks.update({
            "contains_cargo_insulin": "insulin" in low,
            "contains_band_2.0_8.0": ("2.0" in low and "8.0" in low),
            "contains_verdict_accepted": "accepted" in low,
            "cites_shipment_id": gt["shipment_id"].lower() in low,
        })
    elif scenario == "excursion":
        cites_excursion = ("1.2" in (answer or "")) and ("23:45" in (answer or ""))
        checks.update({
            "cites_1.2C_reading": "1.2" in (answer or ""),
            "cites_2345_timestamp": "23:45" in (answer or ""),
            "identifies_low_side": any(w in low for w in
                ("below", "under", "beneath", "less than", "lower", "low excursion", "dropped", "minimum")),
            # A no-excursion claim only counts as a MISSED excursion when the
            # answer does not cite the real excursion ("all other readings
            # remained within..." + citing 1.2 °C/23:45 is correct scoping).
            "does_not_miss_known_excursion": cites_excursion or not bool(_NO_EXCURSION.search(low)),
        })
        # Derived deviations of the REAL excursion are legitimate analysis
        # (e.g. "the ~0.8 °C value" = 2.0 − 1.2) once the excursion is cited.
        if cites_excursion:
            temp_ok2, temp_viol2 = check_temperature_values(answer, extras, derived_ok=True)
            checks["no_fabricated_or_confused_temperatures"] = temp_ok2 and hum_ok
            temp_ok, temp_viol = temp_ok2, temp_viol2
    elif scenario == "delay":
        # Delay evidence IS exposed by VW_SHIPMENTS (expected_arrival_at,
        # arrived_at): claims are verified against the real record
        # (01:37:09 expected, 01:45:09 arrived, 8 minutes late).
        durs = re.findall(r"(?:delayed|late) by (\d+)", low) + \
               re.findall(r"(\d+)[- ]min(?:ute)?s?(?:[- ])?(?:delay|late)", low)
        arrs = re.findall(r"arrived (?:at )?(\d{1,2}:\d{2})", low)
        checks.update({
            "cites_shipment_id": gt["shipment_id"].lower() in low,
            "delay_claims_match_records": all(int(d) == 8 for d in durs) if durs else True,
            "arrival_claims_match_records": all(a.startswith("01:45") for a in arrs) if arrs else True,
        })
    elif scenario == "sop":
        checks.update({
            "cites_shipment_id": gt["shipment_id"].lower() in low,
            "cites_sop_evidence": ("sop" in low) and any(v in (answer or "") for v in
                ("4.0", "auxiliary cooling", "cold-storage", "divert", "cold storage")),
            "no_wrong_band_for_shipment": not bool(re.search(
                r"(?:allowed|configured|permitted) (?:range|band|limit)[^.]{0,40}0\.0", low)),
            # An SOP investigation without SQL shipment evidence is NOT a
            # completed investigation (repair commission §3).
            "has_shipment_sql_evidence": bool(ctx.get("sql_evidence")),
        })
    elif scenario == "weather":
        checks.update({
            "cites_shipment_id": gt["shipment_id"].lower() in low,
            "mentions_weather": "weather" in low,
            "synthetic_or_nonlive_labeling": ("synthetic" in low) or ("not live" in low) or ("external" in low),
            # A weather investigation without a real weather tool result is
            # NOT completed (repair commission §3).
            "weather_tool_result_present": ctx.get("weather_temp") is not None,
        })
    else:
        checks["non_empty"] = bool(answer and answer.strip())

    detail = {"checks": checks,
              "temperature_violations": temp_viol if not temp_ok else [],
              "humidity_violations": hum_viol if not hum_ok else [],
              "completeness_overclaim": comp_hit if not comp_ok else None}
    return {"pass": all(checks.values()), **detail}


def classify_outcome(rec):
    """Five outcome classes (repair commission §3). `rec` is a conversation
    record in the harness format (status, acceptance, answer_excerpt)."""
    status = rec.get("status")
    if status in ("error", "bind_error"):
        return "api_or_recursion_failure"
    acc = rec.get("acceptance") or {}
    if acc.get("pass"):
        return "complete_correct"
    checks = acc.get("checks") or {}
    if (not checks.get("no_fabricated_or_confused_temperatures", True)) or acc.get("temperature_violations"):
        return "incorrect"
    if checks.get("does_not_miss_known_excursion") is False:
        return "incorrect"
    if checks.get("delay_claims_match_records") is False or checks.get("arrival_claims_match_records") is False:
        return "incorrect"
    if _UNAVAILABLE.search((rec.get("answer_excerpt") or "").lower()):
        return "safe_refusal"
    return "partial"
