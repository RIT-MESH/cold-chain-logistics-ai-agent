"""Dashboard least-privilege guardrails (ADR-4/R4): the Streamlit dashboard
must query the FDE_VIEWS read-only views in SQL Server mode — NEVER the
unified.* base tables (Phase 2 grants give application identities SELECT
on FDE_VIEWS only; live-found 2026-11-16 as SQL Server error 229 when the
dashboard still queried base tables).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "src" / "ui.py").read_text(encoding="utf-8")
VIEWS_SQL = (ROOT / "scripts" / "setup_unified_views.sql").read_text(encoding="utf-8")
GRANTS_SQL = (ROOT / "scripts" / "phase2_executed_grants.sql").read_text(encoding="utf-8")

VIEW_MAP = {
    "shipments": "FDE_VIEWS.VW_SHIPMENTS",
    "telemetry": "FDE_VIEWS.VW_TELEMETRY",
    "custody_handoffs": "FDE_VIEWS.VW_CUSTODY",
    "alerts": "FDE_VIEWS.VW_ALERTS",
}


def test_table_helper_maps_to_fde_views_in_mssql_mode():
    for logical, view in VIEW_MAP.items():
        assert f"'{logical}'" in UI and f"'{view}'" in UI, f"{logical} not mapped to its view"
    # the mssql branch must route through _VIEWS, not a unified. prefix
    assert "return _VIEWS[name] if mode=='mssql' else name" in UI


def test_no_mssql_base_table_prefix_remains():
    # no code path may BUILD a 'unified.<table>' reference (the old helper
    # did 'unified.'+name); user-facing hint strings may still SAY that the
    # dashboard never touches unified.* base tables
    code = "\n".join(l for l in UI.splitlines() if not l.lstrip().startswith("#"))
    assert "'unified.'+" not in code, "table helper must not build unified.* prefixes"
    assert '"unified."' not in code and "f'unified." not in code
    # and every fetch() query target must come from the view map in mssql mode
    assert "return _VIEWS[name] if mode=='mssql' else name" in code


def test_every_mapped_view_is_defined_with_needed_columns():
    for view in VIEW_MAP.values():
        assert re.search(r"CREATE OR ALTER VIEW\s+" + re.escape(view) + r"\s", VIEWS_SQL), f"{view} not defined"
    # columns the dashboard selects/filters/orders by (from ui.py call sites)
    needed = {
        "VW_SHIPMENTS": ["shipment_id", "vehicle_id", "departed_at", "receiver_verdict"],
        "VW_TELEMETRY": ["shipment_id", "recorded_at", "sensor_id", "temperature_c", "humidity_pct",
                          "latitude", "longitude", "speed_kmh", "traffic_congestion",
                          "fuel_consumption_l_per_100km", "fuel_level_pct"],
        "VW_CUSTODY": ["shipment_id", "handoff_no"],
        "VW_ALERTS": ["shipment_id", "alert_at"],
    }
    for view, cols in needed.items():
        m = re.search(r"CREATE OR ALTER VIEW FDE_VIEWS\.VW_" + view.split("VW_")[1] + r" AS\s*(.*?);", VIEWS_SQL, re.S)
        assert m, f"VW_{view} body not found"
        for col in cols:
            assert re.search(r"\b" + re.escape(col) + r"\b", m.group(1)), f"VW_{view} missing column {col}"


def test_ui_identity_grant_is_views_only():
    assert "GRANT SELECT ON SCHEMA::FDE_VIEWS TO USR_COLDCHAIN_UI_RO" in GRANTS_SQL
    assert not re.search(r"GRANT SELECT ON SCHEMA::unified", GRANTS_SQL), "no base-table grant may exist"


def test_sqlite_message_not_shown_for_mssql_failures():
    # the generic SQLite initialization hint must appear only in the
    # non-mssql branch of the exception handler (live-found: it mislead
    # during a SQL Server permissions failure)
    m = re.search(r"except Exception as exc:(.*?)^else:", UI + "\nelse:", re.S | re.M)
    seg = m.group(1) if m else UI[-900:]
    assert "=='mssql'" in seg, "exception handler must branch on backend mode"
    sqlite_pos = seg.find("SQLite demo: ensure")
    mssql_pos = seg.find("'mssql'")
    assert sqlite_pos > mssql_pos, "SQLite hint must be inside the non-mssql else-branch"
