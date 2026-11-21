# 04 — Dashboard & Feature Integration Map (requirement 5)

Every agreed feature mapped to the exact tables/views/APIs behind it, with
verification status. Legend: ✅ implemented+locally testable · 🔶 implemented,
unverified against live dependency · 🆕 planned (this blueprint).

## 1. Streamlit operator shell (`src/ui.py`) — feature map

| Feature (sidebar page) | Data source (SQLite demo / MSSQL deploy) | Query location | Status |
|---|---|---|---|
| Overview: shipments/vehicles counts | `shipments` | `src/ui.py:38` | ✅ (SQLite verified live; 🔶 MSSQL mode untested) |
| Overview: readings/alerts totals | `telemetry`, `alerts` | `src/ui.py:39-40` | ✅ |
| Shipment volume by year | `shipments.departed_at` (substr/LEFT by backend) | `src/ui.py:43` | ✅ |
| Delivery outcomes | `shipments.receiver_verdict` | `src/ui.py:45` | ✅ |
| Shipments: detail + recent readings | `shipments`, `telemetry` by `shipment_id` | `src/ui.py:47-50` | ✅ |
| Temperature °C / Humidity % charts | `telemetry.temperature_c/humidity_pct` | `src/ui.py:54-59` | ✅ |
| Fleet GPS map + speed/congestion/fuel lines | `telemetry.latitude/longitude/speed_kmh/traffic_congestion/fuel_*` | `src/ui.py:63-67` | ✅ |
| Custody handoffs | `custody_handoffs` (seal_intact included) | `src/ui.py:71` | ✅ |
| Alerts | `alerts` by shipment | `src/ui.py:72` | ✅ |
| Grafana embed | iframe `GRAFANA_PUBLIC_DASHBOARD_URL` (validated http-local/https only) | `src/ui.py:25-29,73-76` | 🔶 needs live Grafana |
| LibreChat embed | iframe `LIBRECHAT_PUBLIC_URL`, same validation | `src/ui.py:77-81` | 🔶 needs live LibreChat |
| Floci incidents | link-out + explanation of separate incident store | `src/ui.py:82-85` | 🔶 |
| Data provenance | `DATASET_MANIFEST.json` + synthetic disclaimer | `src/ui.py:87-88` | ✅ |

Backend switch: `UNIFIED_DATA_BACKEND=sqlite|mssql` (`src/ui.py:13-24`); MSSQL
mode currently prefixes `unified.` (base tables) — **correction C5:** the
public-facing UI is migrated onto FDE_VIEWS as a required Phase 2 application
change, so no application identity ever holds base-table grants
(`05_SECURITY_MODEL.md` §3).

## 2. Grafana dashboards

### Unified cold-chain (`integrations/grafana/dashboards/unified-cold-chain.json`, uid `cold-chain-unified`)

| Panel | Source view + column | Line |
|---|---|---|
| Avg temperature °C | `FDE_VIEWS.VW_TELEMETRY.temperature_c` daily | :30 |
| Avg humidity % | `VW_TELEMETRY.humidity_pct` | :52 |
| Traffic congestion (simulated) | `VW_TELEMETRY.traffic_congestion` | :74 |
| Fuel consumption L/100km (simulated) | `VW_TELEMETRY.fuel_consumption_l_per_100km` | :96 |
| Alert counts by day | `VW_ALERTS.alert_at` | :118 |
| Average speed km/h | `VW_TELEMETRY.speed_kmh` | :140 |

Datasource `coldchain-sql` (mssql type) — currently uses **SQL_AGENT_USER**
(`provisioning/datasources/mssql.yml:8-10`,
`docker-compose.observability.yml:13-14`) → moves to a dedicated
`USR_GRAFANA_RO` granted on FDE_VIEWS as part of the initial database
deployment (correction C5, gap G-SEC-2; Phases 2–3). Historical analysis:
explicit 2026-07→2026-11 time window, UTC, empty refresh
(`unified-cold-chain.json:6-11`); the whole 2021–2026 range is selectable.
Status: 🔶 (requires live SQL Server + Grafana).

### Telemetry incidents (`telemetry-incidents.json`, uid `cold-chain-incidents`)

| Panel | Source view | Line |
|---|---|---|
| Incidents by provenance | `FDE_VIEWS.VW_TELEMETRY_INCIDENTS` (Provenance split) | :30 |
| Latest 100 incidents | `VW_TELEMETRY_INCIDENTS` TOP 100 | :52 |
| Temperature readings incl. normal | `FDE_VIEWS.VW_TELEMETRY_EVENTS` time-series | :74 |

Status: 🔶 (needs the optional telemetry tables + live Grafana).

## 3. AI assistant (LibreChat → gateway → agent)

| Capability | Interface | Evidence | Status |
|---|---|---|---|
| Chat UI | LibreChat custom endpoint "Cold Chain AI", model `cold-chain-agent` | `integrations/librechat/librechat.yaml` | 🔶 external service, not shipped in compose |
| OpenAI-compatible API | `POST /v1/chat/completions`, `/v1/models`, `/health` | `api/main.py:49-132` | ✅ contract-verified offline (`tests/test_gateway.py`) |
| Streamlit embed of LibreChat | validated iframe + CSP frame-ancestors guidance | `src/ui.py:77-81`, `integrations/librechat/EMBED_IN_STREAMLIT.md` | 🔶 |
| Shipment/telemetry Q&A | `query_telemetry_db` → 7 `FDE_VIEWS` views, TOP≤100 | `src/agent_tools.py:76-120` | ✅ logic / 🔶 live SQL Server |
| Custody & alert Q&A | `VW_CUSTODY`, `VW_ALERTS` | `sql_guard.py:5`; tool docstring | ✅ / 🔶 |
| Vehicle & route Q&A | `VW_VEHICLES`, `VW_ROUTES` | same | ✅ / 🔶 |
| Current weather context | `fetch_corridor_conditions` → Open-Meteo, labeled non-historical | `src/agent_tools.py:122-146` | 🔶 external API |
| SOP recommendations | `search_compliance_sop` → Pinecone (lazy, optional) | `src/agent_tools.py:48-70,148-164` | 🔶 needs PINECONE_API_KEY |
| Response structure | dispatcher format per guidelines §9 | `src/prompts/system_prompt.txt` | ✅ (prompt) / 🔶 (live LLM behavior) |

## 4. Floci incidents visibility (see `03_DATA_AND_EVENT_PROCESSING.md`)

- Grafana incident dashboard (above) — 🔶.
- **Agent queries over incidents: currently impossible** — views not in
  allowlist (gap G-EP-3). 🆕 Phase 5 extension (test-first).
- Streamlit page explains the separate store (`src/ui.py:82-85`) — ✅ text
  only. 🆕 Phase 4 (correction C4): the page additionally lists recent
  incidents from `FDE_VIEWS.VW_TELEMETRY_INCIDENTS` in MSSQL mode, with
  Observed vs Ingested timestamps and the synthetic-stream label; events
  whose `ShipmentId` fails linkage validation are visible via
  `VW_EVENT_LINK_VALIDATION` (see `03_DATA_AND_EVENT_PROCESSING.md` §B1).

## 5. Historical analysis integrity (all components)

- All timestamps rendered UTC (Grafana dashboards set `"timezone": "utc"`).
- Synthetic labels mandatory on UI (`src/ui.py:31,34,85`), dashboards
  ("(simulated)" panel titles; SYNTHETIC in unified dashboard title),
  agent prompt line 1, dataset README. No "LIVE" labels anywhere — verified.
- Alert causality: `alerts.message` distinguishes observed excursions
  (e.g., "Observed 0.5 C outside configured 1 to 6 C",
  `data/unified/alerts.csv` row 1); agent prompt requires distinguishing
  derived alerts from causal evidence (`system_prompt.txt:7`).