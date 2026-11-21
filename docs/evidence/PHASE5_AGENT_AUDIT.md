# Phase 5 — Agent Incident Awareness & Audit (2026-11-17)

**Directive:** operator Phase 5 master directive. Non-destructive implementation;
no Terraform apply/destroy run; no IAM changes; no billable AWS resources created
(CW custom metrics stay inside the 10-metric free tier: 2 reconcile + 4 agent).

## 1. Gap analysis → implemented changes

| Gap (found by inspection) | Resolution |
|---|---|
| SQL guard allowlist excluded the Phase 4 incident views (agent could not see incidents — gap G-EP-3) | `src/sql_guard.py`: +`vw_telemetry_events`, `vw_telemetry_incidents`, `vw_incident_notifications`, `vw_event_link_validation` (ADDITIVE — all existing restrictions unchanged) |
| Tool docstring/prompt had no incident guidance | `src/agent_tools.py` docstring: 4 new views with exact columns + incident examples; honesty rules (derived severity, sns_acked ≠ delivered, synthetic labeling); `src/prompts/system_prompt.txt`: incident-investigation rules incl. "never invent incident records" |
| No execution timeout on agent SQL engine | `connect_args={"timeout": 10}` (+ existing TOP 1-100 guard cap + `fetchmany(10)`) |
| No structured audit trail (only the token_usage line, and it was invisible — C13-F3) | NEW `src/audit.py` + hooks in all three tools + gateway |
| No request correlation id | `new_request()` ContextVar scope per request; every audit line carries `request_id` |
| Gateway container logs went only to Docker json-file | compose `fde-api` now ships to the EXISTING `/coldchain/dev/gateway` CW Logs group via `awslogs` (non-blocking, 1 MB buffer — audit can never stall chat) |
| Third-party INFO loggers silently dropped by uvicorn (C13-F3, confirmed live: zero audit lines after first deploy) | `logging.basicConfig(level=INFO)` in `api/main.py` — audit + token_usage lines now verified landing in CW |
| No agent-audit monitoring | Grafana `cold-chain-agent-audit` dashboard (7 panels) on live CW metrics |
| Durable queryable audit store (ADR-10) | PREPARED, NOT EXECUTED: `scripts/phase5_agent_audit_log.sql` (append-only table + INSERT-only grant to existing USR_AUDIT_WRITER + FDE_VIEWS.VW_AGENT_AUDIT) — awaiting operator migration approval |

## 2. SQL guard additions (all SELECT-only, single view, TOP 1-100)

`FDE_VIEWS.VW_TELEMETRY_INCIDENTS`, `VW_INCIDENT_NOTIFICATIONS`,
`VW_TELEMETRY_EVENTS`, `VW_EVENT_LINK_VALIDATION`. Verified blocked:
unified.* base tables, the Phase 4 base tables (dbo.TelemetryIncidents /
dbo.IncidentNotificationAttempts), cross-database refs, unqualified names,
modifications, EXEC, joins/CTEs/UNION, multi-statement injection, TOP>100.

## 3. Agent audit architecture (§7/§8)

- **Event classes**: `agent_request_end` (status, duration_ms, model,
  tool_events), `agent_tool_event` (tool, outcome executed/guard_rejected/
  error, guard decision, approved views, rows, duration, error class,
  value-elided SQL fingerprint), `auth_failure` (401s; scheme only).
- **Every line**: UTC ISO-8601 `ts_utc`, stable `request_id`, pseudonymous
  actor (`key-<sha256[:12]>` of the gateway API key — never the key).
- **Privacy**: no prompts, no responses, no SQL literals (rejected SQL is
  fingerprinted with values elided), no tokens/cookies/credentials; final
  `_redact()` defense drops any line carrying secret markers.
- **Transport**: Python logging → stdout → `awslogs` (non-blocking) →
  `/coldchain/dev/gateway` (90-day retention from Phase 1). Honest limitation:
  non-blocking mode means a CloudWatch outage can DROP audit lines; the
  buffers are small. NOT tamper-proof — stated per directive §8.
- **Metrics** (request-end aggregates): ColdChain/Phase4 namespace —
  AgentRequests, AgentToolExecutions, AgentGuardRejections, AgentErrors.
  Live-verified: all six custom metrics present; AgentRequests datapoint=1
  after the TTFT probe.

## 4. Live verification performed

1. **Audit pipeline end-to-end** (real request): CW Logs shows
   `cold-chain.audit {"actor": "key-3d01fd4bb9e5", "duration_ms": 249499,
   "event": "agent_request_end", "model": "cold-chain-agent", "request_id":
   "2eeabc06-…", "status": "success", "tool_events": 0, "ts_utc": "…"}` —
   followed by instance-role credential pickup for the metric emission.
2. **Grafana agent-audit dashboard**: loaded (7 panels), CloudWatch
   datasource, live metrics only — no sensitive content.
3. **NIM TTFT probe**: HTTP 200 in 249.8 s ("Say OK") — NIM serving slowly
   tonight (recorded variance; pre-pause plateau was ~150 s). First E2E
   attempt died at `httpcore2.ReadTimeout` after a 20-minute conversation
   (one turn exceeded the 300 s per-turn timeout — external NIM latency, the
   same failure class recorded pre-pause; NOT a Phase 5 defect). A simplified
   E2E retry is in flight; result to be appended below.

## 5. Tests

**Suite 234/234** (was 196; +38 Phase 5 tests across guard extension, audit
generation/correlation/redaction/failure-handling, timeouts/row caps,
deployment wiring, and regression protection for all previous queries).

## 6. Files modified

`src/sql_guard.py`, `src/agent_tools.py`, `src/audit.py` (new),
`api/main.py`, `src/prompts/system_prompt.txt`, `docker/compose.yml`
(fde-api awslogs), `integrations/grafana/dashboards/agent-audit.json` (new),
`scripts/phase5_agent_audit_log.sql` (new, PREPARED only),
`tests/test_phase5_agent_audit.py` (new).

## 7. Operator approval items

1. **E2E result** (pending NIM latency — retry in flight).
2. **Optional durable audit table** (`phase5_agent_audit_log.sql`) —
   additive migration; INSERT-only to the existing USR_AUDIT_WRITER.
3. **CloudWatch logs-read IAM** (same pattern as Dashboard-D): would enable
   Grafana Logs-Insights drill-down panels for audit events. Deferred by
   design until approved.
