"""Deterministic evidence-only fallback answers (operator directive
2026-11-21 §8: reliable degradation handling).

When NVIDIA synthesis fails or is too slow for a SUPPORTED shipment request,
the gateway returns a clearly-identified deterministic summary built from
fresh, fixed, guard-valid SQL (and — for incident+SOP intent — the same
hardened SOP retrieval). The output is:

- HONESTLY LABELED: it states explicitly that AI synthesis was unavailable
  and no AI narration was used (never falsely presented as LLM-generated);
- EVIDENCE-GROUNDED: every fact is rendered from live tool output; no
  fabricated measurements, incidents, citations or place names;
- VALIDATED: api/main.py runs the SAME intent-based validator over the
  fallback text; a failing fallback becomes an honest
  insufficient-evidence response instead of reaching the user;
- GEOGRAPHY-SAFE: coordinates are reported exactly as recorded, with the
  dataset's own caveat (modeled GPS, intentional region mixing) and NEVER a
  place-name label (§5);
- RISK-CAREFUL: recorded risk classifications are reported as recorded, with
  their derivation explicitly unknown, kept separate from envelope
  compliance, confirmed incidents and the receiver verdict (§6).

Unsupported/open-ended questions never get this fallback — the gateway
returns a meaningful unavailability message for those instead.
"""
import ast

from src.audit import emit_audit

FALLBACK_HEADER = ("[Deterministic evidence summary — the AI synthesis service is "
                   "temporarily unavailable, so this summary was assembled directly "
                   "from the authorized database views without AI narration. "
                   "All data is SYNTHETIC TEST DATA and requires human review.]")

GPS_NOTE = ("GPS values are recorded synthetic coordinates; the dataset intentionally "
            "mixes regions and its routes are not confirmed trajectories, so no place "
            "names are attached to them.")

RISK_NOTE = ("risk_level values are recorded synthetic classifications; the data does "
             "not store how they were derived, so no cause is inferred here.")


def _run_sql(sql: str):
    """Run one FIXED query through the guarded tool and parse its text
    output into (columns, rows). Guard/metering/audit behavior is the
    production tool's own."""
    from src.agent_tools import query_telemetry_db
    from src.evidence_prefetch import (
        CUSTODY_SQL, INCIDENT_SQL, NOTIFICATION_SQL, SHIPMENT_SQL,
        TELEMETRY_SQL, TELEMETRY_STATS_SQL,
    )
    out = query_telemetry_db.invoke({"sql_query": sql})
    return _parse_tool_output(out)


def _dt_args_to_str(args_text: str) -> str | None:
    """Render pyodbc's datetime.datetime(2026, 10, 31, 12, 33, 23) constructor
    args as a readable 'YYYY-MM-DD HH:MM:SS' literal. Returns None when the
    args are not plain integers (leave the line untouched then)."""
    parts = [p.strip() for p in args_text.split(",") if p.strip()]
    try:
        nums = [int(p.rstrip("fL")) for p in parts]
    except ValueError:
        return None
    if len(nums) < 3:
        return None
    text = f"{nums[0]:04d}-{nums[1]:02d}-{nums[2]:02d}"
    if len(nums) >= 6:
        text += f" {nums[3]:02d}:{nums[4]:02d}:{nums[5]:02d}"
    elif len(nums) >= 4:
        text += f" {nums[3]:02d}:00:00"
    if len(nums) >= 7:
        text += f".{nums[6]:06d}"
    return f"'{text}'"


def _parse_tool_output(out: str):
    """Parse the tool's 'COLUMNS: ...' + tuple-line text format. pyodbc
    renders DECIMAL/datetime values as Decimal('x') / datetime.datetime(...)
    constructors inside the tuple repr — normalized to plain, readable
    literals before literal_eval (values are displayed exactly as recorded,
    never altered)."""
    import re as _re
    if not isinstance(out, str) or not out.startswith("COLUMNS:"):
        return [], []  # "No records matched" or error text — no structured rows
    lines = out.splitlines()
    columns = [c.strip() for c in lines[0][len("COLUMNS:"):].split(",")]
    rows = []
    for line in lines[1:]:
        line = line.strip()
        if not line.startswith("("):
            continue
        s = _re.sub(r"Decimal\('([^']*)'\)", r"'\1'", line)

        def _dt(m):
            return _dt_args_to_str(m.group(1)) or m.group(0)

        s = _re.sub(r"datetime\.datetime\(([^()]*)\)", _dt, s)
        s = _re.sub(r"datetime\.date\(([^()]*)\)", _dt, s)
        try:
            rows.append(ast.literal_eval(s))
        except (ValueError, SyntaxError):
            continue
    return columns, rows


def _num(value, digits: int = 2) -> str:
    try:
        f = float(value)
        return f"{f:.{digits}f}".rstrip("0").rstrip(".") or "0"
    except (TypeError, ValueError):
        return str(value)


def _load_shipment(sid: str):
    from src.evidence_prefetch import SHIPMENT_SQL
    cols, rows = _run_sql(SHIPMENT_SQL.format(sid=sid))
    if not rows:
        return None
    return dict(zip(cols, rows[0]))


def _load_stats(sid: str):
    from src.evidence_prefetch import TELEMETRY_STATS_SQL
    cols, rows = _run_sql(TELEMETRY_STATS_SQL.format(sid=sid))
    return dict(zip(cols, rows[0])) if rows else {}


def _load_rows(sql_template: str, sid: str):
    cols, rows = _run_sql(sql_template.format(sid=sid))
    return [dict(zip(cols, r)) for r in rows]


def _shipment_lines(sid: str, shipment, stats) -> list:
    lines = [f"Shipment {sid} (synthetic test data):"]
    if shipment:
        cargo = shipment.get("cargo_type", "unknown")
        lines.append(f"- Cargo: {cargo}; vehicle {shipment.get('vehicle_id', 'unknown')}; "
                     f"pallets: {shipment.get('pallets', 'not recorded')}.")
        dep, exp, arr = (shipment.get("departed_at"), shipment.get("expected_arrival_at"),
                         shipment.get("arrived_at"))
        lines.append(f"- Departed {dep}; expected {exp}; arrived {arr or 'not recorded'}.")
        verdict = shipment.get("receiver_verdict", "not recorded")
        lines.append(f"- Receiver verdict: {verdict}.")
        lo, hi = shipment.get("temperature_min_c"), shipment.get("temperature_max_c")
        lines.append(f"- Stated temperature envelope: {_num(lo)} to {_num(hi)} °C.")
    if stats and stats.get("n_readings"):
        lines.append(f"- Telemetry: {stats.get('n_readings')} readings from "
                     f"{stats.get('first_at')} to {stats.get('last_at')}; "
                     f"observed temperature {_num(stats.get('min_c'))} to "
                     f"{_num(stats.get('max_c'))} °C (average {_num(stats.get('avg_c'))} °C).")
        if shipment:
            try:
                lo = float(shipment.get("temperature_min_c"))
                hi = float(shipment.get("temperature_max_c"))
                mn = float(stats.get("min_c"))
                mx = float(stats.get("max_c"))
                if mn >= lo and mx <= hi:
                    lines.append(f"- Temperature-envelope compliance: all observed readings "
                                 f"stayed within the stated envelope (threshold compliance; "
                                 f"this is separate from recorded risk classifications).")
                else:
                    lines.append(f"- Temperature-envelope compliance: observed readings "
                                 f"({mn} to {mx} °C) fell OUTSIDE the stated envelope "
                                 f"({lo} to {hi} °C).")
            except (TypeError, ValueError):
                pass
    return lines


def _incident_lines(sid: str) -> list:
    from src.evidence_prefetch import INCIDENT_SQL, NOTIFICATION_SQL
    incidents = _load_rows(INCIDENT_SQL, sid)
    lines = []
    if not incidents:
        lines.append(f"- Incidents: no temperature-excursion incidents are recorded for "
                     f"{sid}. (No recorded incident is not the same as a clean real-world "
                     f"bill of health — this is synthetic data.)")
        return lines
    for inc in incidents:
        try:
            severity = float(inc.get("TemperatureC")) - float(inc.get("ThresholdC"))
            sev_txt = (f"derived severity {severity:+.1f} °C (TemperatureC "
                       f"{_num(inc.get('TemperatureC'))} minus ThresholdC "
                       f"{_num(inc.get('ThresholdC'))}; severity is derived, not stored)")
        except (TypeError, ValueError):
            sev_txt = "severity could not be derived"
        lines.append(f"- Recorded incident {inc.get('IncidentId')} (status "
                     f"{inc.get('Status')}, provenance {inc.get('Provenance')}): "
                     f"{_num(inc.get('TemperatureC'))} °C observed at "
                     f"{inc.get('ObservedAtUtc')} against a "
                     f"{_num(inc.get('ThresholdC'))} °C threshold; {sev_txt}.")
    notes = _load_rows(NOTIFICATION_SQL, sid)
    for n in notes:
        state = n.get("NotificationState")
        inter = ""
        if state == "sns_acked":
            inter = (" (the SNS topic accepted the notification — this does NOT "
                     "confirm recipient delivery)")
        elif state == "pending":
            inter = " (no acknowledged publish attempt yet)"
        lines.append(f"- Notification state: {state}{inter}; "
                     f"{n.get('AckedCount')} acknowledged of {n.get('AttemptCount')} attempts "
                     f"(last attempt {n.get('LastAttemptAtUtc')}).")
    return lines


def _custody_lines(sid: str) -> list:
    from src.evidence_prefetch import CUSTODY_SQL
    handoffs = _load_rows(CUSTODY_SQL, sid)
    if not handoffs:
        return [f"- Custody: no handoff records for {sid}."]
    seals = all(h.get("seal_intact") in (True, "True", 1, "1", "Yes", "yes") for h in handoffs)
    lines = [f"- Custody: {len(handoffs)} recorded handoffs "
             f"({', '.join(str(h.get('handoff_no')) for h in handoffs)}); "
             f"seals intact: {'all recorded as intact' if seals else 'at least one handoff recorded a broken seal'}."]
    return lines


def _risk_lines(sid: str) -> list:
    from src.evidence_prefetch import TELEMETRY_SQL
    rows = _load_rows(TELEMETRY_SQL, sid)
    highs = [r for r in rows if str(r.get("risk_level", "")).lower() == "high"]
    if highs:
        return [f"- Recorded risk flags: {len(highs)} of the {len(rows)} most recent "
                f"telemetry readings carry a 'high' risk classification. {RISK_NOTE} "
                f"The reason would require examination; this does not by itself mean a "
                f"temperature excursion occurred."]
    return [f"- Recorded risk flags: none of the {len(rows)} most recent readings carry "
            f"a 'high' classification." if rows else
            f"- Recorded risk flags: no telemetry rows were returned for {sid}."]


def _gps_line(sid: str) -> str:
    from src.evidence_prefetch import TELEMETRY_SQL
    rows = _load_rows(TELEMETRY_SQL, sid)
    if rows:
        last = rows[0]
        return (f"- Route GPS (latest recorded reading): lat {last.get('latitude')}, "
                f"lon {last.get('longitude')}. {GPS_NOTE}")
    return f"- Route GPS: no telemetry coordinates returned. {GPS_NOTE}"


def _sop_block(question: str) -> str:
    """Deterministic SOP retrieval for the fallback (§9: exact provenance,
    or an honest unavailability statement — never invented citations)."""
    from src.agent_tools import search_compliance_sop
    from src.evidence_prefetch import sop_query_for
    out = search_compliance_sop.invoke({"query": sop_query_for(question)})
    if "unavailable" in (out or "").lower() or "no sop sections matched" in (out or "").lower():
        return ("- SOP guidance: could not be retrieved at this time (retrieval service "
                "unavailable). No procedures are cited or invented here.")
    return ("- SOP guidance (retrieved reference material; requires human review; "
            "procedural guidance only):\n" + out)


def deterministic_answer(ids, intent: str, question: str) -> str | None:
    """Build the fallback answer for the resolved scope, or None when even
    the shipment record could not be retrieved (insufficient evidence)."""
    ids = list(ids)[:2]
    if not ids:
        return None
    primary = ids[0]
    shipment = _load_shipment(primary)
    if shipment is None:
        emit_audit("agent_tool_event", tool="evidence_fallback", outcome="insufficient_evidence",
                   documents=ids)
        return None
    stats = _load_stats(primary)
    emit_audit("agent_tool_event", tool="evidence_fallback", outcome="executed",
               documents=ids, intent=intent)

    parts = [FALLBACK_HEADER, ""]
    parts.extend(_shipment_lines(primary, shipment, stats))

    if intent in ("incident", "incident_sop"):
        parts.extend(_incident_lines(primary))
        parts.extend(_custody_lines(primary))
        parts.extend(_risk_lines(primary))

    if intent in ("temperature_history", "humidity_history"):
        from src.evidence_prefetch import TELEMETRY_SQL
        rows = _load_rows(TELEMETRY_SQL, primary)
        metric = "humidity_pct" if intent == "humidity_history" else "temperature_c"
        label = "Humidity" if intent == "humidity_history" else "Temperature"
        parts.append(f"- {label} history ({len(rows)} most recent readings, chronological "
                     f"order below; all synthetic test data):")
        for r in reversed(rows):
            parts.append(f"  {r.get('recorded_at')} — {metric} reading "
                         f"{_num(r.get(metric))}; recorded risk classification "
                         f"{r.get('risk_level')}.")
        parts.append(f"- {GPS_NOTE} {RISK_NOTE}")
        parts.extend(_incident_lines(primary))

    if intent == "shipment_report":
        parts.extend(_incident_lines(primary))
        parts.extend(_custody_lines(primary))
        parts.extend(_risk_lines(primary))
        parts.append(_gps_line(primary))

    if intent == "incident_sop":
        parts.append(_sop_block(question))
        parts.append("- The AI synthesis that normally integrates this evidence is "
                     "unavailable; a human should review these facts and the cited "
                     "procedure directly.")

    if intent == "comparison" and len(ids) == 2:
        second = ids[1]
        shipment2 = _load_shipment(second)
        if shipment2 is None:
            parts.append(f"\nComparison: no records were returned for {second}, so the "
                         f"comparison cannot be completed (insufficient evidence for "
                         f"that shipment).")
        else:
            stats2 = _load_stats(second)
            parts.append("")
            parts.extend(_shipment_lines(second, shipment2, stats2))
            parts.extend(_incident_lines(second))
            parts.extend(_custody_lines(second))
            parts.extend(_risk_lines(second))
            # Deterministic conclusion on stated criteria ONLY (§7 categories).
            from src.evidence_prefetch import INCIDENT_SQL
            inc1 = _load_rows(INCIDENT_SQL, ids[0])
            inc2 = _load_rows(INCIDENT_SQL, ids[1])
            if inc1 and not inc2:
                parts.append(f"- Comparison conclusion (stated criteria: recorded "
                             f"temperature-excursion incidents): {ids[0]} has "
                             f"{len(inc1)} recorded incident(s) while {ids[1]} has none — "
                             f"{ids[1]} has fewer confirmed issues on this criterion.")
            elif inc2 and not inc1:
                parts.append(f"- Comparison conclusion (stated criteria: recorded "
                             f"temperature-excursion incidents): {ids[1]} has "
                             f"{len(inc2)} recorded incident(s) while {ids[0]} has none — "
                             f"{ids[0]} has fewer confirmed issues on this criterion.")
            elif not inc1 and not inc2:
                parts.append("- Comparison conclusion (stated criteria: recorded "
                             "temperature-excursion incidents): neither shipment has a "
                             "recorded incident, so no confirmed-issue difference exists; "
                             "recorded risk classifications and envelope margins above are "
                             "the available indicators requiring review.")
            else:
                parts.append("- Comparison conclusion: both shipments have recorded "
                             "incidents; see the incident lines above for the details of "
                             "each — a single ranking is not asserted here because the "
                             "records do not state severity on a comparable scale.")
            parts.append("- Any 'riskier' judgment beyond these stated criteria would "
                         "require human review of the recorded indicators; this is "
                         "synthetic test data.")

    parts.append("- This response is a deterministic evidence summary, not an AI "
                 "synthesis, because the AI service was unavailable; it contains no "
                 "inferences beyond the records shown.")
    return "\n".join(parts)
