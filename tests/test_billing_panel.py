"""Billing panel auto-refresh regression tests (operator directive
2026-11-22). All offline: the generator renders from a fixture snapshot;
the refresh job is source-audited (stdlib-only, bounded, atomic, no
credentials)."""
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location(
    "gen", ROOT / "scripts" / "generate_grafana_dashboards.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

FRESH_SNAPSHOT = {
    "account_id": "123456789012",
    "billing_period": {"start": "2026-11-01", "end": "2026-11-23"},
    "retrieved_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "metric": "UnblendedCost", "granularity": "MONTHLY", "group_by": "SERVICE",
    "currency": "USD", "total_mtd": 15.06, "estimated": True,
    "api_requests": 1,
    "services": [
        {"service": "Amazon Elastic Compute Cloud - Compute", "usd": 3.65},
        {"service": "Amazon Relational Database Service", "usd": 2.33},
        {"service": "Amazon Elastic Load Balancing", "usd": 2.99},
    ],
    "refresh": {"status": "ok", "error_class": None, "error_detail": None, "attempts": 1},
}


# ------------------------------------------------------------ rendering
def test_panel_renders_from_snapshot():
    content = gen.billing_panel_content(FRESH_SNAPSHOT)
    assert "US$ 15.06" in content
    assert "2026-11-01 → 2026-11-23" in content
    assert FRESH_SNAPSHOT["retrieved_at_utc"] in content
    assert "UnblendedCost" in content
    assert "Amazon Elastic Compute Cloud - Compute" in content and "$3.65" in content
    assert "ESTIMATED" in content
    # Operator directives 2026-11-22: no account id and no footer narrative
    assert "123456789012" not in content
    assert "Refreshed by the once-daily host scheduler" not in content
    assert "billing alarm" not in content and "not forecasts" not in content


def test_panel_keeps_freshness_visible_but_never_shows_error_text():
    """Operator directive 2026-11-22 (supersedes the earlier stale-warning
    display): the panel must NOT render refresh-status/error lines or the
    stale/failed warning block — the 'Data retrieved … (snapshot age: X h)'
    line remains the freshness signal, and no raw AWS error text is ever
    shown to dashboard viewers."""
    old = dict(FRESH_SNAPSHOT)
    old["retrieved_at_utc"] = (datetime.now(timezone.utc) -
                               timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = gen.billing_panel_content(old)
    assert "STALE" not in content and "WARNING" not in content
    assert "Refresh status" not in content
    assert "AccessDenied" not in content
    assert "US$ 15.06" in content  # last-good figures still shown
    assert "72." in content or "h)" in content  # age still visible

    failed = dict(FRESH_SNAPSHOT)
    failed["refresh"] = {"status": "error", "error_class": "RuntimeError",
                         "error_detail": "AccessDenied: ce:GetCostAndUsage",
                         "attempts": 2}
    content = gen.billing_panel_content(failed)
    assert "STALE" not in content and "WARNING" not in content
    assert "AccessDenied" not in content and "Refresh status" not in content
    assert "US$ 15.06" in content  # figures retained, no error text rendered


def test_refresh_status_stays_machine_readable_in_snapshot():
    """The snapshot keeps full refresh/error details for OPERATIONS (audit,
    log) — only the PANEL rendering omits them (operator 2026-11-22)."""
    failed = dict(FRESH_SNAPSHOT)
    failed["refresh"] = {"status": "error", "error_class": "AccessDeniedException",
                         "error_detail": "not authorized", "attempts": 2}
    assert failed["refresh"]["status"] == "error"  # recorded, queryable
    content = gen.billing_panel_content(failed)
    assert "not authorized" not in content  # but never rendered


def test_panel_without_snapshot_is_explicit():
    content = gen.billing_panel_content(None)
    assert "not yet generated" in content
    assert "6.22" not in content  # the hardcoded Nov 18 figure is GONE


def test_dashboard_d_uses_snapshot_panel(monkeypatch, tmp_path):
    """The generated dashboard JSON must contain the snapshot-driven billing
    panel — never the old static figures."""
    gen.BILLING_SNAPSHOT = tmp_path / "billing_snapshot.json"
    gen.BILLING_SNAPSHOT.write_text(json.dumps(FRESH_SNAPSHOT), encoding="utf-8")
    content = gen.billing_panel_content(gen._load_billing_snapshot())
    assert "US$ 15.06" in content
    assert "6.22" not in content


# ------------------------------------------------------------ refresh job source audit
def test_refresh_job_is_stdlib_only():
    src = (ROOT / "scripts" / "billing_refresh.py").read_text(encoding="utf-8")
    for banned in ("boto3", "access_key", "secret_key", "session_token"):
        assert banned not in src.lower(), f"billing_refresh.py must not contain {banned!r}"
    assert "aws\", \"ce\", \"get-cost-and-usage" in src or '"aws", "ce"' in src


def test_refresh_job_bounds_and_atomicity():
    src = (ROOT / "scripts" / "billing_refresh.py").read_text(encoding="utf-8")
    assert "MAX_ATTEMPTS = 2" in src       # bounded retries
    assert "MAX_PAGES = 10" in src         # bounded pagination
    assert "os.replace(tmp, SNAPSHOT)" in src  # atomic replacement
    assert "0o600" in src                  # secure file permissions
    # failure keeps the last-good data (only refresh status fields change)
    assert "last GOOD data" in src or "existing" in src


def test_scheduler_is_once_daily_systemd_timer():
    """§5: once-daily collection only. The host has NO cron (AL2023 minimal —
    live-found: no crond, no /etc/cron.d), so the scheduler is a systemd
    timer; Persistent=true gives the missed-run catch-up after an EC2
    stop/start (§6.8 recovery requirement)."""
    svc = (ROOT / "docker" / "grafana" / "coldchain-billing-refresh.service").read_text(encoding="utf-8")
    timer = (ROOT / "docker" / "grafana" / "coldchain-billing-refresh.timer").read_text(encoding="utf-8")
    assert "billing_refresh.py" in svc
    assert "Type=oneshot" in svc
    assert "OnCalendar=*-*-* 09:30:00" in timer   # once daily, host is UTC
    assert "Persistent=true" in timer              # stop/start recovery
    # no credentials ever in the units
    for banned in ("access_key", "secret", "password", "token="):
        assert banned.lower() not in (svc + timer).lower()


# ------------------------------------------------------------ snapshot security
def test_snapshot_schema_and_no_secrets():
    keys = set(FRESH_SNAPSHOT)
    for required in ("account_id", "billing_period", "retrieved_at_utc", "metric",
                     "total_mtd", "services", "currency", "estimated", "refresh"):
        assert required in keys
    blob = json.dumps(FRESH_SNAPSHOT)
    for marker in ("nvapi-", "password", "AWSSecretKey", "authorization"):
        assert marker.lower() not in blob.lower()
    # and the rendered panel carries no secrets either
    content = gen.billing_panel_content(FRESH_SNAPSHOT)
    for marker in ("nvapi-", "password", "secret", "Bearer "):
        assert marker.lower() not in content.lower()
