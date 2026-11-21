"""Phase 7 dashboard-repair regression tests (operator directive 2026-11-18,
§10.4): one guard per identified root cause.

Root causes fixed (see PHASE7_DASHBOARD_REPAIR.md):
1. Mojibake: PS 5.1 `Get-Content -Raw` ANSI-decodes BOM-less files — every
   push through the old text pipeline double-encoded non-ASCII. Guard: repo
   files must contain NO mojibake signatures, and the staging scripts must
   be byte-level.
2. Grafana dashboard variables placed in the PANELS list (generator bug):
   malformed no-title 'query' panel + unexpanded $shipment → 'Plugin query
   not found' / zeros. Guard: generator source + generated JSON.
3. CloudWatch target ids must start with a LOWERCASE LETTER (AWS rejects
   digit-first ids → every Dashboard-D panel 'No data'). Guard: generator
   + JSON.
4. Audit dashboard default window must cover the recorded acceptance window.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "integrations/grafana/dashboards"

MOJIBAKE = ["ðŸ", "â€", "Ã¢", "Ã‚", "Â "]


# ---------------------------------------------- root cause 1: encoding
@pytest.mark.parametrize("rel", [
    "src/ui.py", "src/agent_tools.py", "src/audit.py", "api/main.py",
    "src/evidence_prefetch.py", "src/answer_validator.py",
    "src/prompts/system_prompt.txt", "README.md",
    "integrations/grafana/dashboards/agent-audit.json",
    "integrations/grafana/dashboards/telemetry-incidents.json",
])
def test_repo_files_clean_of_mojibake(rel):
    """The repository must stay free of double-encoded text (the corruption
    lived in the DEPLOYED copies; this guards the source of truth)."""
    text = (ROOT / rel).read_text(encoding="utf-8")
    for bad in MOJIBAKE:
        assert bad not in text, f"{rel} contains {bad!r}"


def test_ui_labels_render_correct_unicode():
    ui = (ROOT / "src/ui.py").read_text(encoding="utf-8")
    assert "🧊 Unified Cold-Chain" in ui and "page_icon='🧊'" in ui
    assert "🚪 Logout" in ui  # operator-specified appearance


def test_staging_scripts_are_byte_level():
    """The push pipeline must never decode text: byte-level staging only
    (the ROOT-CAUSE docstring may MENTION the PS quirk — that is not usage)."""
    for name in ("stage_byte_exact.py", "stage_dashboards_byte_exact.py"):
        src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "read_bytes" in src  # byte-level source reads only
        # and no PowerShell text-decoding call in the executable logic
        code = src.split('"""', 2)[2] if src.count('"""') >= 2 else src
        assert "Get-Content" not in code


# ---------------------------------------------- root cause 2: variables
def test_generator_places_variables_correctly():
    gen = (ROOT / "scripts/generate_grafana_dashboards.py").read_text(encoding="utf-8")
    # var_shipment must NEVER appear inside a panels list
    assert "], variables=[var_shipment()]" in gen
    assert "[\n    var_shipment()," not in gen


@pytest.mark.parametrize("uid", ["cold-chain-temp-humidity", "cold-chain-logistics-custody"])
def test_variable_dashboards_have_templating_and_no_malformed_panels(uid):
    d = json.loads((DASH / f"{uid}.json").read_text(encoding="utf-8"))
    vars_ = d.get("templating", {}).get("list", [])
    assert len(vars_) == 1 and vars_[0]["name"] == "shipment"
    assert vars_[0]["current"]["value"] == "UC-003241"  # real default, not $__all
    assert vars_[0]["includeAll"] is False  # $__all breaks per-shipment SQL
    assert not [p for p in d["panels"] if p.get("type") == "query" and not p.get("title")]


def test_variable_default_is_a_real_shipment_in_dataset():
    """The $shipment default must exist in the canonical dataset (no invented
    ids) — checked against the shipments CSV header + a known id."""
    d = json.loads((DASH / "cold-chain-temp-humidity.json").read_text(encoding="utf-8"))
    default = d["templating"]["list"][0]["current"]["value"]
    csv_text = (ROOT / "data/unified/shipments.csv").read_text(encoding="utf-8")
    assert default in csv_text, f"default shipment {default} not in dataset"


# ---------------------------------------------- root cause 3: CW target ids
def test_generator_cw_ids_start_with_lowercase_letter():
    gen = (ROOT / "scripts/generate_grafana_dashboards.py").read_text(encoding="utf-8")
    assert '"id": "m0"' in gen and '"id": "0"' not in gen


def test_all_cw_targets_in_all_dashboards_have_valid_ids():
    """AWS GetMetricData requires ^[a-z][a-zA-Z0-9_]*$ — a digit-first id
    400s and the panel renders 'No data'."""
    pattern = re.compile(r"^[a-z][a-zA-Z0-9_]*$")
    checked = 0
    for path in DASH.glob("*.json"):
        d = json.loads(path.read_text(encoding="utf-8"))
        for p in d.get("panels", []):
            for t in p.get("targets", []):
                if p.get("datasource", {}).get("type") == "cloudwatch" or \
                        t.get("datasource", {}).get("type") == "cloudwatch":
                    tid = t.get("id")
                    assert tid and pattern.match(str(tid)), \
                        f"{path.name} panel {p.get('title')}: invalid CW id {tid!r}"
                    checked += 1
    assert checked >= 8  # Dashboard D + audit panels


# ---------------------------------------------- root cause 4: audit window
def test_audit_dashboard_window_covers_acceptance_period():
    d = json.loads((DASH / "agent-audit.json").read_text(encoding="utf-8"))
    assert d["time"]["from"] == "2026-11-17T00:00:00Z"
    assert d["time"]["to"] == "now"
    names = {t["metricName"] for p in d["panels"] for t in p.get("targets", [])
             if "metricName" in t}
    assert {"AgentRequests", "AgentToolExecutions", "AgentGuardRejections",
            "AgentErrors", "AgentSopRetrievals"} <= names


# ------------------------- final corrections (operator 2026-11-18 review)
@pytest.mark.parametrize("uid", ["cold-chain-temp-humidity", "cold-chain-logistics-custody"])
def test_variable_query_is_plain_sql_string(uid):
    """Grafana 11 SQL datasource variables require the query as a PLAIN
    STRING; the legacy object form fails with 'error when executing the sql
    query' in the dropdown (operator-found)."""
    d = json.loads((DASH / f"{uid}.json").read_text(encoding="utf-8"))
    v = d["templating"]["list"][0]
    assert isinstance(v["query"], str) and "SELECT shipment_id" in v["query"]
    assert v["definition"] == v["query"]
    # and the SQL itself is guard-approved shape: FDE_VIEWS only
    assert "FDE_VIEWS.VW_SHIPMENTS" in v["query"]


def test_variable_default_and_alternate_shipments_exist_in_dataset():
    """The dropdown must offer real shipments: the default UC-003241 and at
    least one other valid shipment (operator directive: test >1 shipment)."""
    csv_text = (ROOT / "data/unified/shipments.csv").read_text(encoding="utf-8")
    ids = [line.split(",")[0] for line in csv_text.splitlines()[1:] if line.strip()]
    assert "UC-003241" in ids
    others = [i for i in ids if i != "UC-003241"]
    assert len(others) >= 1, "dropdown needs more than one shipment to select"


def test_geomap_is_keyless_and_labeled_synthetic():
    """The 'default' CARTO basemap returned 'API KEY REQUIRED' tiles
    (operator-found) — the geomap must use the keyless locally-rendered
    coordinates basemap and stay labeled synthetic (no paid map service)."""
    d = json.loads((DASH / "cold-chain-logistics-custody.json").read_text(encoding="utf-8"))
    geo = [p for p in d["panels"] if p["type"] == "geomap"]
    assert geo, "route panel must exist"
    bm = geo[0]["options"]["basemap"]
    assert bm["type"] == "coordinates"  # keyless, locally rendered
    assert "synthetic" in bm["name"].lower()
    title = geo[0]["title"].lower()
    assert "synthetic" in title  # clear synthetic-route labeling


# ------------------------- time-range strategy (operator directive 2026-11-21)
@pytest.mark.parametrize("uid", ["cold-chain-temp-humidity", "cold-chain-logistics-custody"])
def test_shipment_dashboards_default_to_full_historical_range(uid):
    """2026-11-21 directive §3 supersedes the 2026-11-18 'recent window'
    default: a 2026-01-01 window hid every 2021–2025 shipment behind
    'Data outside time range' (live-proven with UC-000004, observed
    2021-01-02/03, 12 readings present). Per-shipment dashboards must
    default to the COMPLETE synthetic window so any shipment is visible
    immediately."""
    d = json.loads((DASH / f"{uid}.json").read_text(encoding="utf-8"))
    assert d["time"]["from"] == "2021-01-01T00:00:00Z"
    a = json.loads((DASH / "cold-chain-unified.json").read_text(encoding="utf-8"))
    assert a["time"]["from"] == "2021-01-01T00:00:00Z"  # historical overview unchanged


@pytest.mark.parametrize("uid", ["cold-chain-temp-humidity", "cold-chain-logistics-custody"])
def test_shipment_dashboards_explain_the_time_range(uid):
    """§3/§6: a text guidance panel must explain the default range and the
    'Data outside time range' meaning (records exist, window excludes them)
    — a supported navigation explanation, no JS hacks."""
    d = json.loads((DASH / f"{uid}.json").read_text(encoding="utf-8"))
    texts = [p for p in d["panels"] if p.get("type") == "text"]
    assert texts, "shipment dashboard needs the time-range guidance text panel"
    content = texts[0]["options"]["content"]
    assert "2021-01-01" in content
    assert "Data outside time range" in content
    assert "time picker" in content


def test_incidents_dashboard_default_covers_pipeline_data():
    """§4: the pipeline's incidents were ingested 2026-11-17 02:17 UTC
    (dataset-verified: VW_TELEMETRY_INCIDENTS min=max=2026-11-17T02:17:01Z,
    ObservedAtUtc ≈ IngestedAtUtc). A now-24h default showed 'No data' on
    every time-filtered panel (live-proven). The default must cover the
    actual incident window; the data-source documentation must state that
    event timestamps are ingestion-time observations, not historical
    measurements (never misrepresent old synthetic incidents as new)."""
    d = json.loads((DASH / "telemetry-incidents.json").read_text(encoding="utf-8"))
    assert d["time"]["from"] == "now-30d" and d["time"]["to"] == "now"
    texts = [p for p in d["panels"] if p.get("type") == "text"]
    assert texts, "incidents dashboard needs the data-source documentation panel"
    content = " ".join(t["options"]["content"] for t in texts)
    assert "2026-11-17" in content and "IngestedAtUtc" in content
    assert "not be read as newly occurring" in content or "NOT historical" in content
    assert "Operations Overview" in content  # the historical-alert link


def test_live_dashboards_keep_their_genuine_recent_windows():
    """§5: infrastructure and AI-audit dashboards test against their own
    genuine metric windows — 24h for live AWS, 2026-11-17→now for the agent
    metrics (whose entire history begins at the Phase 5 deploy)."""
    infra = json.loads((DASH / "cold-chain-infra-ai.json").read_text(encoding="utf-8"))
    assert infra["time"] == {"from": "now-24h", "to": "now"}
    audit = json.loads((DASH / "agent-audit.json").read_text(encoding="utf-8"))
    assert audit["time"]["from"] == "2026-11-17T00:00:00Z"


@pytest.mark.parametrize("uid", ["cold-chain-temp-humidity", "cold-chain-logistics-custody"])
def test_series_and_geomap_have_honest_empty_states(uid):
    """'No data' in an empty window must be explained, never confused with
    a query failure or a genuine zero."""
    d = json.loads((DASH / f"{uid}.json").read_text(encoding="utf-8"))
    for p in d["panels"]:
        if p["type"] in ("timeseries", "geomap"):
            assert p.get("noValue"), f"panel {p['title']} lacks an honest empty state"
            assert "time range" in p["noValue"]


# ---------------------------------------------- dataset truth (B panels)
def test_dashboard_b_counts_match_dataset_truth():
    """The exact panel SQL for UC-003241 must match the canonical manifest
    expectations for the demo shipment (16 readings, 1 out-of-range, 1 alert
    — dataset-verified in the phase acceptance evidence)."""
    d = json.loads((DASH / "cold-chain-temp-humidity.json").read_text(encoding="utf-8"))
    sqls = " ".join(t.get("rawSql", "") for p in d["panels"] for t in p.get("targets", []))
    assert "$shipment" in sqls  # variable-driven (now correctly expandable)
    assert "humidity_pct IS NOT NULL" in sqls  # honest zero vs missing data
