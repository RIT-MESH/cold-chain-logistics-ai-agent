"""Phase 5 tests (operator directive §10, 2026-11-17): incident-aware agent,
SQL guard extension, and structured auditing. All offline; no live DB.

Sections map to the directive's numbered requirements 1-14.
"""
import json
import logging
from unittest.mock import patch

import pytest

from src.audit import (AUDIT_LOGGER, _pseudonymize, _redact, audit_request_end,
                       audit_tool_event, current_counters, current_request_id, emit_audit,
                       new_request, put_request_metrics, sql_fingerprint)
from src.sql_guard import ALLOWED_VIEWS, validate_query

CF = "https://d36yix30egjffn.cloudfront.net"


# ---------------------------------------------- §10.1: authorized incident queries
@pytest.mark.parametrize("q", [
    "SELECT TOP 20 IncidentId, ShipmentId, ObservedAtUtc, TemperatureC, ThresholdC, Provenance, Status FROM FDE_VIEWS.VW_TELEMETRY_INCIDENTS WHERE ShipmentId = 'UC-003241'",
    "SELECT TOP 20 IncidentId, NotificationState, AttemptCount, AckedCount, LastAttemptAtUtc FROM FDE_VIEWS.VW_INCIDENT_NOTIFICATIONS WHERE ShipmentId = 'UC-003241'",
    "SELECT TOP 50 EventId, ObservedAtUtc, TemperatureC, Provenance, IngestedAtUtc FROM FDE_VIEWS.VW_TELEMETRY_EVENTS WHERE ShipmentId = 'UC-003241' ORDER BY ObservedAtUtc",
    "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_EVENT_LINK_VALIDATION",
    "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_TELEMETRY_INCIDENTS WHERE Provenance = 'synthetic'",
])
def test_incident_views_allowed(q):
    assert validate_query(q) == q


def test_all_phase4_views_in_allowlist():
    for view in ("vw_telemetry_events", "vw_telemetry_incidents",
                 "vw_incident_notifications", "vw_event_link_validation"):
        assert view in ALLOWED_VIEWS
    # additive only: every original view remains
    for view in ("vw_shipments", "vw_telemetry", "vw_custody", "vw_alerts",
                 "vw_sensors", "vw_vehicles", "vw_routes"):
        assert view in ALLOWED_VIEWS


# ---------------------------------------------- §10.2: unauthorized table access
@pytest.mark.parametrize("q", [
    "SELECT TOP 10 * FROM unified.shipments",
    "SELECT TOP 10 IncidentId FROM dbo.TelemetryIncidents",           # Phase 4 base table
    "SELECT TOP 10 AttemptId FROM dbo.IncidentNotificationAttempts",   # base ledger table
    "SELECT TOP 10 EventId FROM TelemetryEvents",                     # unqualified
    "SELECT TOP 10 shipment_id FROM otherdb.FDE_VIEWS.VW_SHIPMENTS",  # cross-database
    "SELECT TOP 10 shipment_id FROM FDE_VIEWS.VW_AGENT_AUDIT",         # not in allowlist (prepared script not executed)
])
def test_unauthorized_access_blocked(q):
    with pytest.raises(ValueError):
        validate_query(q)


# ---------------------------------------------- §10.3: injection / multi-statement
@pytest.mark.parametrize("q", [
    "SELECT TOP 1 EventId FROM FDE_VIEWS.VW_TELEMETRY_INCIDENTS; DROP TABLE unified.shipments",
    "SELECT TOP 1 EventId FROM FDE_VIEWS.VW_TELEMETRY_INCIDENTS' OR '1'='1",
    "WITH cte AS (SELECT 1 AS x) SELECT x FROM FDE_VIEWS.VW_TELEMETRY_INCIDENTS",
    "SELECT TOP 1 a.IncidentId FROM FDE_VIEWS.VW_TELEMETRY_INCIDENTS a JOIN FDE_VIEWS.VW_INCIDENT_NOTIFICATIONS b ON a.IncidentId=b.IncidentId",
    "SELECT TOP 1 IncidentId FROM FDE_VIEWS.VW_TELEMETRY_INCIDENTS UNION SELECT TOP 1 EventId FROM FDE_VIEWS.VW_TELEMETRY_EVENTS",
])
def test_injection_and_multistatement_blocked(q):
    with pytest.raises(ValueError):
        validate_query(q)


# ---------------------------------------------- §10.4: modification attempts
@pytest.mark.parametrize("q", [
    "DELETE FROM FDE_VIEWS.VW_TELEMETRY_INCIDENTS",
    "UPDATE FDE_VIEWS.VW_INCIDENT_NOTIFICATIONS SET NotificationState = 'sns_acked'",
    "INSERT INTO FDE_VIEWS.VW_TELEMETRY_EVENTS (EventId) VALUES ('x')",
    "DROP TABLE FDE_VIEWS.VW_EVENT_LINK_VALIDATION",
    "ALTER VIEW FDE_VIEWS.VW_TELEMETRY_INCIDENTS AS SELECT 1 AS x",
    "EXEC sp_getapplock @Resource='x', @LockMode='Exclusive'",
])
def test_modification_attempts_blocked(q):
    with pytest.raises(ValueError):
        validate_query(q)


# ---------------------------------------------- §10.5-6: docstring examples (lookup forms)
def _tool_doc():
    import src.agent_tools as tools
    return tools.query_telemetry_db.description or tools.query_telemetry_db.__doc__ or ""


def _tool_source():
    from pathlib import Path
    return Path("src/agent_tools.py").read_text(encoding="utf-8")


def test_docstring_incident_examples_pass_guard():
    import re
    text = _tool_doc()
    # every documented SELECT example must be guard-valid (no bait examples)
    examples = [m.strip() for m in re.findall(r"- (SELECT [^\n]+)", text)]
    assert len(examples) >= 6, "docstring must document the incident examples"
    for ex in examples:
        if not ex.upper().startswith("SELECT"):
            continue
        assert validate_query(ex) == ex, f"docstring example rejected by guard: {ex[:70]}"


def test_incident_views_documented_with_columns():
    text = _tool_doc()
    for view, col in [("VW_TELEMETRY_INCIDENTS", "ThresholdC"),
                      ("VW_INCIDENT_NOTIFICATIONS", "NotificationState"),
                      ("VW_TELEMETRY_EVENTS", "Provenance"),
                      ("VW_EVENT_LINK_VALIDATION", "IngestedAtUtc")]:
        assert view in text and col in text, f"{view}/{col} must be documented"
    # honesty rules in the docstring
    assert "NOT that email was delivered" in text
    assert "derive it from TemperatureC minus ThresholdC" in text


# ---------------------------------------------- §10.7: missing/unknown incident handling
def test_prompt_forbids_invented_incidents():
    from pathlib import Path
    prompt = Path("src/prompts/system_prompt.txt").read_text(encoding="utf-8")
    assert "never invent incident records" in prompt
    assert "sns_acked" in prompt and "never that email was delivered" in prompt
    assert "label it as derived" in prompt  # severity is derived, not stored


# ---------------------------------------------- §10.8: linkage validation view
def test_linkage_validation_queries():
    assert validate_query("SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_EVENT_LINK_VALIDATION") == \
           "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_EVENT_LINK_VALIDATION"
    with pytest.raises(ValueError):
        validate_query("SELECT TOP 5 * FROM FDE_VIEWS.VW_EVENT_LINK_VALIDATION")


# ---------------------------------------------- §10.9-10: audit events + correlation ids
def test_audit_event_generation(caplog):
    with caplog.at_level(logging.INFO, logger="cold-chain.audit"):
        rid = new_request("test-key-material")
        emit_audit("agent_tool_event", tool="query_telemetry_db", outcome="executed", rows=3)
    line = next(r.getMessage() for r in caplog.records if r.name == "cold-chain.audit")
    rec = json.loads(line)
    assert rec["event"] == "agent_tool_event" and rec["request_id"] == rid
    assert rec["ts_utc"].endswith("Z") and "T" in rec["ts_utc"]      # UTC ISO
    assert rec["actor"].startswith("key-") and len(rec["actor"]) == 16
    assert rec["rows"] == 3
    # correlation: a second event in the same scope carries the SAME id
    with caplog.at_level(logging.INFO, logger="cold-chain.audit"):
        audit_tool_event("query_telemetry_db", "guard_rejected", guard_decision="Disallowed SQL operation")
    rec2 = json.loads(caplog.records[-1].getMessage())
    assert rec2["request_id"] == rid == current_request_id()


def test_actor_pseudonym_is_stable_not_invertible():
    a = _pseudonymize("super-secret-key")
    assert a == _pseudonymize("super-secret-key") and "super-secret-key" not in a
    assert _pseudonymize(None) == "anonymous"


# ---------------------------------------------- §10.11: secret redaction
def test_secret_redaction():
    assert _redact('{"x": "nvapi-abc123"}') == "[redacted]"
    assert _redact('{"x": "Authorization: Bearer eyJhbGci"}') == "[redacted]"
    assert _redact('{"event": "agent_tool_event", "rows": 2}') == '{"event": "agent_tool_event", "rows": 2}'


def test_sql_fingerprint_elides_values():
    fp = sql_fingerprint("SELECT TOP 10 x FROM FDE_VIEWS.VW_TELEMETRY_INCIDENTS WHERE ShipmentId = 'UC-003241'")
    assert "UC-003241" not in fp and "VW_TELEMETRY_INCIDENTS" in fp


# ---------------------------------------------- §10.12: logging failure handling
def test_audit_never_raises_and_metrics_fail_silently():
    with patch("src.audit.AUDIT_LOGGER") as broken_logger:
        broken_logger.info.side_effect = RuntimeError("log sink down")
        emit_audit("agent_tool_event", outcome="executed")  # must not raise
    with patch("boto3.client", side_effect=RuntimeError("no creds")):
        put_request_metrics()                                # must not raise
    # a broken audit path does not corrupt the request counters
    new_request("k")
    audit_tool_event("query_telemetry_db", "executed")
    assert current_counters()["tool_executions"] == 1


def test_request_end_audit_and_counters(caplog):
    new_request("k")
    audit_tool_event("query_telemetry_db", "executed")
    audit_tool_event("query_telemetry_db", "guard_rejected", guard_decision="x")
    with caplog.at_level(logging.INFO, logger="cold-chain.audit"):
        with patch("src.audit.put_request_metrics") as pm:    # no network in tests
            audit_request_end(status="success", duration_ms=123, model="cold-chain-agent", tool_events=1)
        pm.assert_called_once()
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec["event"] == "agent_request_end" and rec["status"] == "success"
    assert current_counters()["requests"] == 1


# ---------------------------------------------- §10.13: timeouts + row limits
def test_engine_has_execution_timeout_and_row_cap():
    src_text = _tool_source()
    assert 'connect_args={"timeout": 10}' in src_text    # execution timeout
    assert "fetchmany(10)" in src_text                   # hard row cap on fetch
    # guard-level TOP cap (existing) still enforced
    with pytest.raises(ValueError):
        validate_query("SELECT TOP 101 IncidentId FROM FDE_VIEWS.VW_TELEMETRY_INCIDENTS")


# ---------------------------------------------- §10.14: regression — existing queries
def test_existing_shipment_queries_still_pass():
    for q in [
        "SELECT TOP 10 shipment_id, cargo_type FROM FDE_VIEWS.VW_SHIPMENTS",
        "SELECT COUNT(*) AS count FROM FDE_VIEWS.VW_TELEMETRY",
        "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_TELEMETRY WHERE temperature_c > 4",
    ]:
        assert validate_query(q) == q


# ---------------------------------------------- deployment wiring
def test_compose_ships_gateway_logs_to_cloudwatch():
    from pathlib import Path
    compose = Path("docker/compose.yml").read_text(encoding="utf-8")
    svc = compose.split("fde-api:")[1].split("streamlit:")[0]
    assert "awslogs-group: /coldchain/dev/gateway" in svc
    assert "mode: non-blocking" in svc          # audit can never stall chat


def test_agent_audit_dashboard_exists_with_live_metrics_only():
    from pathlib import Path
    dash = json.loads(Path("integrations/grafana/dashboards/agent-audit.json").read_text(encoding="utf-8"))
    assert dash["uid"] == "cold-chain-agent-audit"
    metrics = {t["metricName"] for p in dash["panels"] for t in p.get("targets", [])
               if "metricName" in t}
    assert {"AgentRequests", "AgentToolExecutions", "AgentGuardRejections", "AgentErrors"} <= metrics
    text = json.dumps(dash)
    assert "no prompts" in text.lower() and "start-query" in text  # privacy + working CLI queries documented
    # 2026-11-17 live-found: per-event Logs Insights panels are BLOCKED by a
    # Grafana-plugin vs AWS new-query-engine incompatibility — the dashboard
    # must state that honestly, not show broken panels.
    assert "BLOCKED" in text and "Successfully queried the CloudWatch logs API" in text
    # and no Logs-Insights query targets remain (no error panels rendered)
    assert not [t for p in dash["panels"] for t in p.get("targets", []) if t.get("queryMode") == "Logs"]


def test_optional_audit_table_script_is_insert_only():
    from pathlib import Path
    sql = Path("scripts/phase5_agent_audit_log.sql").read_text(encoding="utf-8")
    assert "PREPARED, NOT EXECUTED" in sql
    assert "GRANT SELECT, INSERT ON dbo.AgentAuditLog TO USR_AUDIT_WRITER" in sql
    low = sql.lower()
    for bad in ("grant update", "grant delete"):
        assert bad not in low
