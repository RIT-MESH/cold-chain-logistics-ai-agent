"""Guardrails for the four completed dashboard sections (2026-11-16):
AI Assistant (LibreChat), Grafana Monitoring, Data Provenance, Floci
Incidents. Enforces: public URLs configured on the CloudFront hostname;
read-only manifest mount; provenance errors never give SQL advice; the
Floci viewer reads the approved incident view and shows an honest
not-yet-activated empty state; Grafana uses its dedicated identity.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "src" / "ui.py").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker" / "compose.yml").read_text(encoding="utf-8")
DS_YML = (ROOT / "integrations" / "grafana" / "provisioning" / "datasources" / "mssql.yml").read_text(encoding="utf-8")
CF = "https://d36yix30egjffn.cloudfront.net"


def test_public_urls_configured_on_cloudfront_hostname():
    stream = COMPOSE.split("streamlit:")[1].split("librechat:")[0]
    assert f'LIBRECHAT_PUBLIC_URL: "{CF}/chat/"' in stream
    assert f'GRAFANA_PUBLIC_DASHBOARD_URL: "{CF}/grafana/d/cold-chain-unified/unified-cold-chain"' in stream
    assert f'GRAFANA_PUBLIC_INCIDENT_URL: "{CF}/grafana/d/cold-chain-incidents/telemetry-incidents"' in stream


def test_assistant_and_grafana_sections_render_links_and_frames():
    assert "Open the AI assistant in a new tab" in UI
    assert "Open Grafana in a new tab" in UI
    assert "SEPARATELY AUTHENTICATED" in UI  # LibreChat own-login disclosure


def test_manifest_mounted_read_only():
    stream = COMPOSE.split("streamlit:")[1].split("librechat:")[0]
    assert "./data/unified/DATASET_MANIFEST.json:/app/data/unified/DATASET_MANIFEST.json:ro" in stream


def test_provenance_missing_file_error_is_file_specific():
    m = re.search(r"except FileNotFoundError:\s*\n\s*st\.error\((.*?)\)\n", UI, re.S)
    assert m, "provenance must handle FileNotFoundError with its own st.error"
    msg = m.group(1)
    assert "DATASET_MANIFEST.json" in msg
    assert "SQL" not in msg and "permission" not in msg.lower()  # no SQL advice


def test_floci_viewer_uses_approved_incident_view():
    assert "'telemetry_incidents':'FDE_VIEWS.VW_TELEMETRY_INCIDENTS'" in UI
    assert "SELECT IncidentId,ShipmentId,ObservedAtUtc,TemperatureC,ThresholdC,Provenance,Status" in UI
    # honest empty state: pipeline-not-activated wording, no live-feed claim
    # (Phase 4 upgrade 2026-11-17: functional viewer + derived notification
    # state, with the same not-activated honesty while no events exist)
    assert "not yet activated" in UI.lower()
    assert "no live operational feed" in UI.lower()
    # Phase 4: derived notification state + linkage audit are surfaced too
    assert "VW_INCIDENT_NOTIFICATIONS" in UI and "VW_EVENT_LINK_VALIDATION" in UI
    # sqlite demo mode must not query the (SQL Server only) incident view
    seg = UI.split("'Floci Incidents'")[1].split("else:")[0]
    assert "UNIFIED_DATA_BACKEND" in seg  # mode check guards the query


def test_grafana_datasource_uses_dedicated_identity():
    assert "${SQL_GRAFANA_USER}" in DS_YML and "${SQL_GRAFANA_PASSWORD}" in DS_YML
    assert "${SQL_AGENT_USER}" not in DS_YML  # no longer the agent identity


def test_grafana_sql_tls_verification_never_disabled():
    # RDS TLS verification must stay ON (operator rule) — the trust anchor
    # is the canonical AWS global bundle, volume-mounted read-only
    assert "encrypt: 'true'" in DS_YML
    assert "tlsConfigurationMethod: file" in DS_YML
    assert "sslRootCertFile: /etc/grafana/rds-global-bundle.pem" in DS_YML
    grafana = COMPOSE.split("grafana:")[1].split("oauth2-proxy:")[0] if "oauth2-proxy:" in COMPOSE else COMPOSE
    assert "./integrations/grafana/rds-global-bundle.pem:/etc/grafana/rds-global-bundle.pem:ro" in COMPOSE
    assert "TrustServerCertificate" not in DS_YML  # never trust without verification here


def test_grafana_same_origin_embedding_allowed():
    # Without GF_SECURITY_ALLOW_EMBEDDING, Grafana sends X-Frame-Options:
    # deny and the dashboard's iframe shows a broken gray box (live-found
    # 2026-11-16). Auth is NOT relaxed by this — login still required.
    grafana = COMPOSE.split("grafana:")[1]
    assert 'GF_SECURITY_ALLOW_EMBEDDING: "true"' in grafana
    # and the embedding stays same-origin: the iframe URL is on the
    # CloudFront hostname, same as the parent page
    assert 'GRAFANA_PUBLIC_DASHBOARD_URL: "https://d36yix30egjffn.cloudfront.net/grafana/d/' in COMPOSE


def test_librechat_self_registration_stays_disabled():
    # LibreChat is internet-reachable via CloudFront with its own auth —
    # open self-registration must remain off (live-found 2026-11-16: this
    # LibreChat 1.2.x gates registration ONLY on the ALLOW_REGISTRATION
    # env var; the yaml registration block is NOT read).
    import re as _re
    m = _re.search(r"(?ms)^  librechat:$\n(.*?)(?=^  \w[\w-]*:$|\Z)", COMPOSE)
    assert m, "librechat service block not found"
    assert 'ALLOW_REGISTRATION: "false"' in m.group(1)


def test_librechat_refresh_secret_in_secret_fetch():
    # Without JWT_REFRESH_SECRET, LibreChat logins fail with
    # "generateRefreshToken: secretOrPrivateKey must have a value"
    fetch = (ROOT / "docker" / "fetch-secrets.sh").read_text(encoding="utf-8")
    assert 'emit JWT_REFRESH_SECRET "$(openssl rand -hex 32)"' in fetch
