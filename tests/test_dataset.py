"""Phase 0 (objective 7): verify the bundled unified dataset against DATASET_MANIFEST.json.

Offline: reads data/unified/unified_cold_chain.sqlite and the manifest only.
"""
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/unified/unified_cold_chain.sqlite"
MANIFEST = json.loads((ROOT / "data/unified/DATASET_MANIFEST.json").read_text(encoding="utf-8"))

TABLES = ["shipments", "vehicles", "sensors", "telemetry", "custody_handoffs", "alerts", "route_events"]


def test_dataset_files_exist():
    for name in TABLES:
        assert (ROOT / "data/unified" / f"{name}.csv").exists(), f"missing CSV: {name}"
    assert DB.exists(), "bundled SQLite database missing"


def test_table_counts_match_manifest():
    with sqlite3.connect(DB) as conn:
        for table in TABLES:
            expected = MANIFEST["counts"][f"{table}.csv"]
            actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert actual == expected, f"{table}: expected {expected}, got {actual}"


def test_foreign_key_check_clean():
    with sqlite3.connect(DB) as conn:
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert violations == [], f"foreign key violations: {violations[:5]}"


def test_date_coverage_within_manifest_bounds():
    with sqlite3.connect(DB) as conn:
        # Timestamps are TEXT ISO-ish strings; lexicographic comparison is valid.
        checks = {
            "shipments.departed_at": "SELECT MIN(departed_at), MAX(departed_at) FROM shipments",
            "telemetry.recorded_at": "SELECT MIN(recorded_at), MAX(recorded_at) FROM telemetry",
            "alerts.alert_at": "SELECT MIN(alert_at), MAX(alert_at) FROM alerts",
            "custody_handoffs.handed_at": "SELECT MIN(handed_at), MAX(handed_at) FROM custody_handoffs",
        }
        for label, sql in checks.items():
            low, high = conn.execute(sql).fetchone()
            assert low is not None and high is not None, f"{label}: empty date range"
            assert str(low) >= MANIFEST["date_min"], f"{label}: min {low} before manifest bound"
            assert str(high) <= MANIFEST["date_max"], f"{label}: max {high} after manifest bound"


def test_yearly_shipment_counts_match_manifest():
    with sqlite3.connect(DB) as conn:
        rows = dict(
            conn.execute(
                "SELECT substr(departed_at,1,4) AS year, COUNT(*) FROM shipments GROUP BY year"
            ).fetchall()
        )
    assert rows == {str(y): n for y, n in MANIFEST["yearly_shipments"].items()}, rows


def test_shipment_ids_are_unique_and_referenced():
    """Core relational invariants: unique shipment PK; telemetry references existing shipments."""
    with sqlite3.connect(DB) as conn:
        total, distinct = conn.execute(
            "SELECT COUNT(shipment_id), COUNT(DISTINCT shipment_id) FROM shipments"
        ).fetchone()
        assert total == distinct
        orphans = conn.execute(
            "SELECT COUNT(*) FROM telemetry t "
            "WHERE t.shipment_id NOT IN (SELECT shipment_id FROM shipments)"
        ).fetchone()[0]
        assert orphans == 0, f"{orphans} telemetry rows reference unknown shipments"


def test_synthetic_labels_present():
    """Every shipment and telemetry row carries the synthetic provenance columns."""
    with sqlite3.connect(DB) as conn:
        unlabeled_shipments = conn.execute(
            "SELECT COUNT(*) FROM shipments WHERE synthetic_test_data IS NULL"
        ).fetchone()[0]
        unlabeled_telemetry = conn.execute(
            "SELECT COUNT(*) FROM telemetry WHERE synthetic_test_data IS NULL"
        ).fetchone()[0]
    assert unlabeled_shipments == 0 and unlabeled_telemetry == 0