# Phase 7 — Dashboard Repair Report (2026-11-18, FINAL — operator review round 2)

Round 1 (same day) fixed: the PS 5.1 encoding pipeline (mojibake), the
missing `$shipment` variable, digit-first CloudWatch ids, and the audit
time window. This round addresses the operator's screenshot review.

## Round-2 corrections (all root-caused, all live-verified)

| Operator finding | Root cause | Fix + live verification |
|---|---|---|
| Shipment dropdowns: "error when executing the sql query" | The variable used the LEGACY OBJECT query form `{"query": {...}, "rawQuery": True}` — Grafana 11 SQL datasources require a **plain SQL string** (the PANEL SQL was never the problem; the variable query was) | `var_shipment()` emits a plain string + `definition`. **Verified: the variable query returns 4,020 shipments through the deployed datasource; second shipment UC-003240 returns its 47 readings** (its 2026-window series is correctly empty — its readings predate 2026, showing the honest noValue state) |
| Route GPS panel: "API KEY REQUIRED" | Grafana's "default" basemap is CARTO-hosted, which now rejects anonymous tile requests | Replaced with the **keyless, locally rendered `coordinates` basemap**; panel stays clearly labeled "synthetic route model". No external map service, no keys, no cost |
| Six-year axis compression | Dashboard default spanned 2021→now, compressing ~16 per-shipment readings | Per-shipment dashboards (B, C) now default to **2026-01-01→now** (the demo/recent shipments' window); the **time picker preserves the full 2021–2026 range**; the Operations Overview (A) keeps the full historical span |
| Valid zero vs empty vs failure | — | All series/geomap panels now carry an explicit `noValue` message ("No readings in the selected time range — widen the time picker"); stat panels show genuine 0s (audit GuardRejections/Errors = honest 0 over the window) |
| Dashboard time drift (A served Jul–Nov 2026) | `editable: True` let a browser save create a shadowing saved copy in Grafana's DB | All generator-produced dashboards are now `editable: False` (file = single source of truth; browser time-picking unaffected); A re-provisioned to the file version (**2021-01-01→now, 13 panels, read-only**) |

## Final live verification (same plugin path the browser uses)

- Variable query: plain SQL ✓, default UC-003241 ✓, 4,020 options ✓, second
  shipment works ✓
- Geomap: coordinates basemap ✓, synthetic-labeled ✓
- Infra: **all 6 CW metrics 288 datapoints each** (EC2 CPU/NetworkOut, RDS
  CPU/Connections/FreeableMemory, ALB RequestCount) — IAM unchanged
- Audit window: AgentRequests 15, ToolExecutions 31, SopRetrievals 8,
  GuardRejections 0 (honest), Errors 0 (honest)
- All six served dashboards: 0 mojibake, 0 malformed panels
- Streamlit containers carry `🧊` / `🚪 Logout` byte-verified

**Suite: 310/310** (282 → 302 → 310: +20 round-1 repair tests, +8 round-2
tests — variable string format, keyless geomap, readable windows, honest
empty states, multi-shipment dropdown).

## Operator browser acceptance checklist (final visual pass)

1. Streamlit sidebar: `🧊 Unified Cold-Chain`, `🚪 Logout` on every section;
   logout → 403 on Back/Refresh → re-login works
2. Temp & Humidity: dropdown lists 4,020 shipments (default UC-003241,
   switching works — pick an older shipment and the panels update); stats
   16/1/16/1; series readable in the 2026 window; pick "2021–2026" in the
   time picker for the full range; humidity 0s explained where applicable
3. Logistics & Custody: dropdown works; GPS trace renders on the keyless
   coordinate basemap (clearly labeled synthetic); no "API KEY REQUIRED"
4. Infrastructure: six live CW lines over the last 24 h
5. AI Agent Audit: request/tool/SOP metrics visible from 2026-11-17; no
   `â€` anywhere; drill-down limitation documented truthfully
6. Any panel with an empty window shows the explanatory noValue text —
   not a bare "No data"

Automated verification used the browser's exact datasource/plugin path per
panel; browser acceptance per directive §11 remains the operator's.

