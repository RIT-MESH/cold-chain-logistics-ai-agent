"""Generate the four Phase-3 Grafana dashboards (professional cold-chain
observability set). All SQL targets the FDE_VIEWS read-only views via the
USR_GRAFANA_RO datasource — never unified.* base tables. Dashboard D uses
the CloudWatch datasource (real AWS metrics only; nothing fabricated).

Run from the repo root: python scripts/generate_grafana_dashboards.py
Writes: integrations/grafana/dashboards/*.json
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "integrations" / "grafana" / "dashboards"
DS = {"type": "mssql", "uid": "coldchain-sql"}
CW = {"type": "cloudwatch", "uid": "coldchain-aws"}
SYNTH = "SYNTHETIC TEST DATA — generated dataset 2021–Oct 2026; not real vehicle observations."

GRID = {"left": 0, "top": 0}


def stat(title, sql, x, y, w=4, h=4, unit="short", desc="", datasource=DS, extra=None):
    t = {"id": 0, "refId": "A", "datasource": datasource, "rawSql": sql, "format": "table", "editorMode": "code"}
    if extra:
        t.update(extra)
    return {
        "type": "stat", "title": title, "description": desc or SYNTH,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": datasource,
        "targets": [t],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area"},
        "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
    }


def timeseries(title, sql, x, y, w=12, h=8, unit="short", desc="", datasource=DS, thresholds=None):
    t = {"id": 0, "refId": "A", "datasource": datasource, "rawSql": sql, "format": "time_series", "editorMode": "code"}
    return {
        "type": "timeseries", "title": title, "description": desc or SYNTH,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": datasource,
        "targets": [t],
        "fieldConfig": {
            "defaults": {"unit": unit, "custom": {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 8, "showPoints": "auto", "spanNulls": True}},
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
        # Honest empty state (operator directive: distinguish valid zero vs
        # empty window vs query failure — this text only shows when the
        # query succeeds but the window has no rows)
        "noValue": "No readings in the selected time range — widen the time picker (full range 2021–2026).",
    }


def table(title, sql, x, y, w=12, h=8, desc="", datasource=DS):
    return {
        "type": "table", "title": title, "description": desc or SYNTH,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": datasource,
        "targets": [{"id": 0, "refId": "A", "datasource": datasource, "rawSql": sql, "format": "table", "editorMode": "code"}],
    }


def pie(title, sql, x, y, w=6, h=8, desc=""):
    return {
        "type": "piechart", "title": title, "description": desc or SYNTH,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": DS,
        "targets": [{"id": 0, "refId": "A", "datasource": DS, "rawSql": sql, "format": "table", "editorMode": "code"}],
        "options": {"pieType": "donut", "displayLabels": ["name", "percent"], "legend": {"displayMode": "table", "placement": "right"}},
    }


def geomap(title, sql, x, y, w=12, h=8, desc=""):
    # Keyless basemap (operator-found 2026-11-18: the CARTO-hosted 'default'
    # basemap returned "API KEY REQUIRED" tiles). The 'coordinates' basemap
    # is rendered locally by Grafana with zero external calls or licenses —
    # appropriate for a clearly-labeled SYNTHETIC route trace.
    return {
        "type": "geomap", "title": title, "description": desc or SYNTH,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": DS,
        "targets": [{"id": 0, "refId": "A", "datasource": DS, "rawSql": sql, "format": "table", "editorMode": "code"}],
        "options": {"view": {"lat": 22, "lon": 79, "zoom": 4}, "layers": [{"type": "markers", "config": {"showLegend": False}}],
                    "basemap": {"type": "coordinates", "name": "Coordinates (keyless; synthetic route data)"}},
        "noValue": "No GPS points in the selected time range — widen the picker (full range 2021–2026).",
    }


def var_shipment(default="UC-003241"):
    """Per-shipment dashboard variable. MUST be passed via the `variables=`
    argument of dashboard() — passing it into the panels list produced a
    malformed no-title 'query' panel and left $shipment unexpanded
    (operator-found 2026-11-18: 'Plugin query not found', all-zero stats).
    includeAll=False: $__all breaks per-shipment SQL equality filters.
    The query is a PLAIN SQL STRING (operator-found 2026-11-18: the legacy
    {'query': {...}, 'rawQuery': True} OBJECT form makes Grafana 11 SQL
    datasources fail with 'error when executing the sql query' in the
    dropdown)."""
    return {
        "name": "shipment", "label": "Shipment", "type": "query", "datasource": DS,
        "query": "SELECT shipment_id FROM FDE_VIEWS.VW_SHIPMENTS ORDER BY shipment_id",
        "definition": "SELECT shipment_id FROM FDE_VIEWS.VW_SHIPMENTS ORDER BY shipment_id",
        "refresh": 1, "includeAll": False, "multi": False,
        "current": {"text": default, "value": default},
        "options": [],
    }


def dashboard(uid, title, panels, variables=None, time_from="2021-01-01T00:00:00Z", time_to="now"):
    """Default time range strategy (operator directive 2026-11-21 §5):
    historical + shipment-specific dashboards default to the COMPLETE
    synthetic dataset window (2021-01-01 → now) so pre-2026 shipments
    (e.g. UC-000004, observed 2021-01-02/03) are immediately visible instead
    of hitting 'Data outside time range' (the 2026-01-01 default from the
    2026-11-18 iteration is superseded — it hid every 2021–2025 shipment
    behind an empty-looking chart). Live infrastructure/audit dashboards
    pass their own recent windows explicitly. Users narrow the time picker
    to zoom into a shipment's observation window."""
    return {
        "uid": uid, "title": title, "tags": ["cold-chain", "synthetic-test-data"], "style": "dark",
        "timezone": "browser", "schemaVersion": 41, "refresh": "5m",
        # Read-only in the UI: file provisioning is the single source of
        # truth. (Live-found: with editable=True a browser "save" created a
        # shadowing saved copy whose default time range diverged from the
        # file.) Time-picking/filtering remain fully available in the UI.
        "editable": False,
        "time": {"from": time_from, "to": time_to},
        "templating": {"list": variables or []},
        "panels": panels,
        "description": SYNTH,
    }


def time_guidance_panel(w=24, h=3, extra=""):
    """Header text panel (operator directive 2026-11-21 §3/§6): explains the
    default range and the navigation path — a chart reading 'Data outside
    time range' means rows EXIST but sit outside the visible window."""
    return {
        "type": "text", "title": "Time-range guide",
        "gridPos": {"h": h, "w": w, "x": 0, "y": 0},
        "options": {"mode": "markdown", "content":
            "**Default range = the complete synthetic dataset (2021-01-01 → today)** so every "
            "shipment's readings are visible immediately — including 2021–2025 shipments. "
            "To zoom into one shipment's observation window, narrow the **time picker** "
            "(top right). A panel showing *\"Data outside time range\"* means rows exist but "
            "sit outside the visible window — not missing data and not a query failure. "
            "'No readings in the selected time range' under a chart means the query succeeded "
            "but the window has no rows." + extra},
    }


def sql_metric(target, metric, namespace, dimension, dims, stat="Average", period="300", region="us-east-1"):
    """Grafana 11.6 CloudWatch target in the FULL MODERN frontend format.

    Live-found (operator browser round 3, 2026-11-18): the browser frontend
    re-serializes provisioned targets through the CW datasource's query
    model. Legacy-shaped targets (dimensions map + statistic only, no
    queryMode/matchAll/dimensionFilters) LOSE their dimensions/mode in that
    model — the browser shows 'No data' while the same target sent directly
    to /api/ds/query returns full datapoints. The fix carries BOTH the
    modern fields (queryMode, matchAll, expression, dimensionFilters) and
    the legacy fields (dimensions map, statistic, alias) so the backend AND
    the frontend both have their native shapes.
    The query id must also start with a lowercase letter (AWS rejects
    digit-first ids with a downstream ValidationError)."""
    dimension_filters = [{"key": k, "value": v,
                          "filterOption": {"key": k, "operator": "=", "value": v}}
                         for k, v in dims.items()]
    return {
        "id": "m0", "refId": "A", "datasource": CW,
        "namespace": namespace, "metricName": metric, "statistic": stat, "period": period,
        "region": region, "dimensions": dict(dims), "dimensionFilters": dimension_filters,
        "matchAll": False, "expression": "", "queryMode": "Metrics",
        # THE browser-vs-API fix (grafana/grafana#118482): without these two
        # fields the Grafana UI query editor SILENTLY NEVER FIRES the CW
        # query — panels show "No data" in the browser while the identical
        # target works via /api/ds/query (operator-found live 2026-11-18).
        "metricQueryType": 0, "metricEditorMode": 0,
        "alias": target,
    }


# ---------------------------------------------------------------- A: Operations Overview
A = dashboard("cold-chain-unified", "Cold-Chain Operations Overview (SYNTHETIC TEST DATA)", [
    stat("Total shipments", "SELECT COUNT(*) AS shipments FROM FDE_VIEWS.VW_SHIPMENTS", 0, 0),
    stat("Vehicles", "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_VEHICLES", 4, 0),
    stat("Accepted", "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_SHIPMENTS WHERE receiver_verdict='accepted'", 8, 0, {"colorMode": "value"}),
    stat("Accepted with note", "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_SHIPMENTS WHERE receiver_verdict='accepted with note'", 12, 0),
    stat("Rejected", "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_SHIPMENTS WHERE receiver_verdict='rejected'", 16, 0),
    stat("Alerts", "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_ALERTS", 20, 0),
    stat("Temperature excursions", "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_ALERTS WHERE alert_type='temperature_excursion'", 0, 4),
    stat("Sensor / GPS readings", "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_TELEMETRY", 4, 4),
    stat("Custody handoffs", "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_CUSTODY", 8, 4),
    stat("Late arrivals (>expected)", "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_SHIPMENTS WHERE arrived_at > expected_arrival_at", 12, 4),
    timeseries("Shipment volume over time (monthly departures)",
               "SELECT DATEADD(MONTH, DATEDIFF(MONTH, 0, departed_at), 0) AS time, COUNT(*) AS shipments "
               "FROM FDE_VIEWS.VW_SHIPMENTS GROUP BY DATEADD(MONTH, DATEDIFF(MONTH, 0, departed_at), 0) ORDER BY time",
               0, 8, w=18, h=9),
    pie("Delivery outcomes", "SELECT receiver_verdict AS metric, COUNT(*) AS value FROM FDE_VIEWS.VW_SHIPMENTS GROUP BY receiver_verdict", 18, 8, w=6, h=9),
    table("Recent alerts (latest 20)",
          "SELECT TOP 20 alert_at, shipment_id, alert_type, severity, message FROM FDE_VIEWS.VW_ALERTS ORDER BY alert_at DESC",
          0, 17, w=24, h=8),
])

# ---------------------------------------------------------------- B: Temperature and Humidity
B = dashboard("cold-chain-temp-humidity", "Temperature and Humidity (SYNTHETIC TEST DATA)", [
    time_guidance_panel(h=4),
    stat("Configured range °C",
         "SELECT CAST(temperature_min_c AS varchar(10)) + ' – ' + CAST(temperature_max_c AS varchar(10)) AS rng "
         "FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id='$shipment'",
         0, 4, desc="Per-shipment configured range from the shipment record."),
    stat("Readings", "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_TELEMETRY WHERE shipment_id='$shipment'", 4, 4),
    stat("Out-of-range readings",
         "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_TELEMETRY t JOIN FDE_VIEWS.VW_SHIPMENTS s ON s.shipment_id=t.shipment_id "
         "WHERE t.shipment_id='$shipment' AND (t.temperature_c < s.temperature_min_c OR t.temperature_c > s.temperature_max_c)", 8, 4),
    stat("Humidity observations",
         "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_TELEMETRY WHERE shipment_id='$shipment' AND humidity_pct IS NOT NULL",
         12, 4, desc="0 means this shipment has no humidity data — not a query failure."),
    stat("Alerts on this shipment", "SELECT COUNT(*) AS n FROM FDE_VIEWS.VW_ALERTS WHERE shipment_id='$shipment'", 16, 4),
    timeseries("Temperature °C (configured limits as thresholds 2.0 / 8.0)",
               "SELECT recorded_at AS time, temperature_c FROM FDE_VIEWS.VW_TELEMETRY "
               "WHERE shipment_id='$shipment' AND temperature_c IS NOT NULL ORDER BY recorded_at",
               0, 8, w=24, h=9, unit="celsius"),
    timeseries("Humidity %",
               "SELECT recorded_at AS time, humidity_pct FROM FDE_VIEWS.VW_TELEMETRY "
               "WHERE shipment_id='$shipment' AND humidity_pct IS NOT NULL ORDER BY recorded_at",
               0, 17, w=24, h=8, unit="percent"),
    timeseries("Readings per day (coverage gaps appear as missing bars)",
               "SELECT CAST(CAST(recorded_at AS date) AS datetime2) AS time, COUNT(*) AS readings "
               "FROM FDE_VIEWS.VW_TELEMETRY WHERE shipment_id='$shipment' GROUP BY CAST(CAST(recorded_at AS date) AS datetime2) ORDER BY time",
               0, 25, w=12, h=9),
    table("Out-of-range readings",
          "SELECT TOP 50 t.recorded_at, t.temperature_c, s.temperature_min_c, s.temperature_max_c, "
          "CASE WHEN t.temperature_c < s.temperature_min_c THEN 'below' ELSE 'above' END AS direction "
          "FROM FDE_VIEWS.VW_TELEMETRY t JOIN FDE_VIEWS.VW_SHIPMENTS s ON s.shipment_id=t.shipment_id "
          "WHERE t.shipment_id='$shipment' AND (t.temperature_c < s.temperature_min_c OR t.temperature_c > s.temperature_max_c) "
          "ORDER BY t.recorded_at", 12, 25, w=12, h=9),
], variables=[var_shipment()])
# thresholds for the temperature panel (configured cold-chain range)
B["panels"][6]["fieldConfig"]["defaults"]["thresholds"] = {
    "mode": "absolute", "steps": [
        {"color": "red", "value": None}, {"color": "green", "value": 2}, {"color": "red", "value": 8}
    ]
}

# ---------------------------------------------------------------- C: Logistics and Custody
C = dashboard("cold-chain-logistics-custody", "Logistics and Custody (SYNTHETIC TEST DATA)", [
    time_guidance_panel(h=4),
    table("Shipment timeline",
          "SELECT shipment_id, cargo_type, pallets, vehicle_id, departed_at, expected_arrival_at, arrived_at, receiver_verdict, "
          "DATEDIFF(MINUTE, expected_arrival_at, arrived_at) AS delay_minutes "
          "FROM FDE_VIEWS.VW_SHIPMENTS WHERE shipment_id='$shipment'", 0, 4, w=14, h=6),
    pie("Delivery acceptance outcomes (all shipments)",
        "SELECT receiver_verdict AS metric, COUNT(*) AS value FROM FDE_VIEWS.VW_SHIPMENTS GROUP BY receiver_verdict", 14, 4, w=10, h=6),
    table("Custody handoffs",
          "SELECT handoff_no, received_by, handed_at, seal_intact FROM FDE_VIEWS.VW_CUSTODY "
          "WHERE shipment_id='$shipment' ORDER BY handoff_no", 0, 10, w=12, h=8),
    timeseries("Speed (km/h) — modeled GPS route data",
               "SELECT recorded_at AS time, speed_kmh FROM FDE_VIEWS.VW_TELEMETRY "
               "WHERE shipment_id='$shipment' AND speed_kmh IS NOT NULL ORDER BY recorded_at",
               12, 10, w=12, h=8, unit="velocitykmh"),
    timeseries("Fuel level % and traffic congestion (synthetic/modeled values)",
               "SELECT recorded_at AS time, fuel_level_pct FROM FDE_VIEWS.VW_TELEMETRY "
               "WHERE shipment_id='$shipment' AND fuel_level_pct IS NOT NULL ORDER BY recorded_at",
               0, 18, w=12, h=8, unit="percent"),
    timeseries("Traffic congestion index (modeled)",
               "SELECT recorded_at AS time, traffic_congestion FROM FDE_VIEWS.VW_TELEMETRY "
               "WHERE shipment_id='$shipment' AND traffic_congestion IS NOT NULL ORDER BY recorded_at",
               12, 18, w=12, h=8),
    geomap("Route GPS trace (synthetic route model)",
           "SELECT TOP 500 latitude AS lat, longitude AS lon, recorded_at "
           "FROM FDE_VIEWS.VW_TELEMETRY WHERE shipment_id='$shipment' AND latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY recorded_at",
           0, 26, w=24, h=10),
], variables=[var_shipment()])

# ---------------------------------------------------------------- D: Infrastructure and AI Monitoring (real AWS metrics)
# Billing panel (operator directive 2026-11-22): rendered from the daily
# Cost Explorer snapshot at integrations/grafana/billing_snapshot.json
# (written atomically by scripts/billing_refresh.py via host cron) — never
# static figures. Falls back to an explicit "not yet generated" state.
from datetime import datetime, timezone

BILLING_SNAPSHOT = OUT.parent / "billing_snapshot.json"
STALE_AFTER_HOURS = 48


def _load_billing_snapshot():
    try:
        return json.loads(BILLING_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _billing_age_hours(snapshot):
    try:
        retrieved = datetime.fromisoformat(snapshot["retrieved_at_utc"].replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - retrieved
        return max(0.0, round(age.total_seconds() / 3600, 1))
    except (KeyError, ValueError, TypeError):
        return None


def billing_panel_content(snapshot):
    """Markdown for the billing panel (§4): MTD total, service breakdown,
    retrieval timestamp, coverage, snapshot age, refresh status and a clear
    stale-data warning when the daily refresh has failed or aged out."""
    if not snapshot:
        return ("**AWS billing snapshot not yet generated.** The daily Cost Explorer job "
                "(scripts/billing_refresh.py, host cron) has not produced "
                "`billing_snapshot.json` yet. Until it runs, no billing figures are shown — "
                "the previous hardcoded November 18 report has been removed.")
    age = _billing_age_hours(snapshot)
    refresh = snapshot.get("refresh", {})
    status = refresh.get("status", "unknown")
    period = snapshot.get("billing_period", {})
    services = snapshot.get("services", [])
    lines = [
        f"**Month-to-date AWS charges: US$ {snapshot.get('total_mtd', 0):.2f}** "
        f"({snapshot.get('currency', 'USD')}, metric `{snapshot.get('metric', 'UnblendedCost')}`)",
        "",
        f"Billing period: **{period.get('start')} → {period.get('end')}** (Cost Explorer `End` is "
        f"exclusive — the period covers through the day before it).",
        f"Data retrieved: **{snapshot.get('retrieved_at_utc', '?')}** "
        f"(snapshot age: **{age if age is not None else '?'} h**) — account-level totals "
        "covering all regions and all services.",
        f"Estimated-data status: **{'ESTIMATED (Cost Explorer preliminary figures)' if snapshot.get('estimated') else 'final'}**.",
        "",
        "| Service | MTD |",
        "|---|---|",
    ]
    for s in services:
        if s["usd"] > 0:
            lines.append(f"| {s['service']} | ${s['usd']:.2f} |")
    # Operator directives 2026-11-22: no refresh-status/error text, no
    # stale-warning block, no account id, and no footer narrative on the
    # rendered panel — the retrieval timestamp + age line is the freshness
    # signal; refresh/error details stay machine-readable in the snapshot.
    return "\n".join(lines)


def billing_panel(snapshot, x=0, y=27):
    return {
        "type": "text", "title": "Estimated AWS charges (account-level, Cost Explorer)",
        "gridPos": {"h": 10, "w": 24, "x": x, "y": y},
        "options": {"mode": "markdown", "content": billing_panel_content(snapshot)},
    }
def cw_panel(kind, title, target, x, y, w=8, h=8, unit="short", desc=""):
    base = {
        "title": title, "gridPos": {"h": h, "w": w, "x": x, "y": y}, "datasource": CW,
        "description": desc or "Real AWS CloudWatch metrics for the live deployment (not synthetic).",
        # Honest empty state: CloudWatch legitimately returns no datapoints
        # for some metrics (e.g. zero 5xx errors) — say so instead of a bare
        # "No data" (operator directive: accurate unavailable-data states).
        "noValue": "No CloudWatch datapoints in range for this metric/dimension",
    }
    if kind == "timeseries":
        base.update({"type": "timeseries", "targets": [target],
                     "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 1, "fillOpacity": 8}}, "overrides": []},
                     "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}})
    else:
        base.update({"type": "stat", "targets": [target],
                     "options": {"reduceOptions": {"calcs": ["lastNotNull"]}},
                     "fieldConfig": {"defaults": {"unit": unit}, "overrides": []}})
    return base


EC2 = {"InstanceId": "i-0123456789abcdef0"}
RDSI = {"DBInstanceIdentifier": "coldchain-dev-sqlexpress"}
ALB = {"LoadBalancer": "app/coldchain-dev-web/6f239ed1528c3e72"}

D = dashboard("cold-chain-infra-ai", "Infrastructure and AI Monitoring (live AWS)", [
    {
        "type": "text", "title": "Live AWS monitoring", "gridPos": {"h": 3, "w": 24, "x": 0, "y": 0},
        "options": {"mode": "markdown", "content":
            "**Real CloudWatch metrics** for the EC2 app host, RDS SQL Server, and the public ALB. "
            "NIM inference latency and token usage are **not yet published to CloudWatch** (LangGraph "
            "gateway logging is a recorded Phase 3/4 follow-up; the C12 counter exists in the API "
            "response) — no panel here fabricates those values."},
    },
    cw_panel("timeseries", "EC2 app host — CPU utilization %",
             sql_metric("CPU", "CPUUtilization", "AWS/EC2", "InstanceId", EC2), 0, 3),
    cw_panel("timeseries", "EC2 app host — network out (bytes)",
             sql_metric("NetOut", "NetworkOut", "AWS/EC2", "InstanceId", EC2, stat="Sum"), 8, 3, unit="Bps"),
    cw_panel("timeseries", "RDS — CPU utilization %",
             sql_metric("RDS CPU", "CPUUtilization", "AWS/RDS", "DBInstanceIdentifier", RDSI), 16, 3),
    cw_panel("timeseries", "RDS — database connections",
             sql_metric("Conns", "DatabaseConnections", "AWS/RDS", "DBInstanceIdentifier", RDSI), 0, 11, unit="short"),
    cw_panel("timeseries", "RDS — freeable memory (bytes)",
             sql_metric("Mem", "FreeableMemory", "AWS/RDS", "DBInstanceIdentifier", RDSI), 8, 11, unit="bytes"),
    cw_panel("timeseries", "ALB — requests (sum)",
             sql_metric("Reqs", "RequestCount", "AWS/ApplicationELB", "LoadBalancer", ALB, stat="Sum"), 16, 11, unit="short"),
    cw_panel("timeseries", "ALB — 5xx responses",
             sql_metric("5xx", "HTTPCode_ELB_5XXCount", "AWS/ApplicationELB", "LoadBalancer", ALB, stat="Sum"), 0, 19, unit="short"),
    cw_panel("timeseries", "ALB — target response time (s)",
             sql_metric("RT", "TargetResponseTime", "AWS/ApplicationELB", "LoadBalancer", ALB), 8, 19, unit="s"),
    cw_panel("timeseries", "ALB — healthy hosts (app target group)",
             sql_metric("AppTG", "HealthyHostCount", "AWS/ApplicationELB", "TargetGroup",
                        {"TargetGroup": "targetgroup/coldchain-dev-app/2b96a2cc6998f5fe",
                         "LoadBalancer": "app/coldchain-dev-web/6f239ed1528c3e72"}, stat="Maximum"),
             16, 19, unit="short"),
    # Billing (operator directive 2026-11-22): rendered from the daily Cost
    # Explorer snapshot — NEVER static figures. See billing_panel() above.
    billing_panel(_load_billing_snapshot()),
], variables=[])
D["time"] = {"from": "now-24h", "to": "now"}

for d in (A, B, C, D):
    p = OUT / (d["uid"] + ".json")
    p.write_text(json.dumps(d, indent=1), encoding="utf-8")
    print("wrote", p)
print("done")
