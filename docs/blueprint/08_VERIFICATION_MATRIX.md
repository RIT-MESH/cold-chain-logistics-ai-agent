# 08 — Verification Matrix (requirement 9)

Revision 5 (2026-11-03: SNS Standard/FIFO correction R5-1, at-least-once
duplicate semantics R5-2, `authenticate-cognito` terminology R5-3). Prior
corrections preserved (C1–C15, R3-1–R3-10, R4-1–R4-5). Statuses (mutually
exclusive per feature): **Implemented** · **Locally verified** ·
**Unverified** · **Planned**.

## A. Core application

| Feature | Status | Evidence |
|---|---|---|
| Streamlit UI (SQLite demo mode) | Locally verified (data layer) | `src/ui.py`; SQLite counts + FK validated this review |
| Streamlit UI (MSSQL mode) | Unverified; **view migration planned Phase 2** (C5 — never base-table grants) | `src/ui.py:13-24` |
| FastAPI gateway (health/models/chat/SSE) | Locally verified | `tests/test_gateway.py` passes |
| Gateway "streaming" is delayed whole-answer | Implemented (known limitation) | `api/main.py:118-132` |
| LangGraph reasoner→tools loop | Implemented; live run gated | `src/orchestrator.py:88-101` |
| LLM provider factory, no silent fallback | Locally verified (source contract) | `src/orchestrator.py:33-77`; `tests/test_llm_provider.py` (passes on Linux; Windows-local fail is a test encoding defect — G-HY-1, fixed Phase 0) |
| GLM-5.3 Flash live auth + tool-calling | **VERIFIED 2026-11-15 — Phase 0 gate (C13) CLOSED (ledger R6-3)**: `check_nim.py` PASS + one live FastAPI→LangGraph→NIM conversation, HTTP 200 in 95.0 s, guard enforced, no credential/reasoning leak (`docs/evidence/C13_LIVE_GATEWAY_VERIFICATION.md`) | `scripts/check_nim.py` (replaced check_ollama_cloud.py at R6-1); archived live evidence in docs/evidence/ |
| GLM-5.3 live SQL tool-calling | Unverified — gated at Phase 3 smoke (RDS + app live) | `09` Phase 3 |
| NIM usage token accounting | **Implemented at R6-1 (C12)** — per-request `usage` incl. reasoning tokens in the gateway response + structured `token_usage` log line; NIM usage fields live-confirmed (U-6 resolved; C13 evidence 2026-11-15) | `api/main.py`; `docs/NVIDIA_NIM_RUNTIME.md` |
| SQL guard allowlist | Locally verified | `tests/test_sql_guard.py` |
| Offline test suite | Locally verified 20/21 | run this review; 1 Windows-only encoding failure |

## B. Data platform

| Feature | Status | Evidence |
|---|---|---|
| Unified CSV dataset | Implemented (counts verified) | `data/unified/*.csv` matches manifest |
| SQLite DB integrity | Locally verified | 7 tables FK-clean; counts = `DATASET_MANIFEST.json` |
| SQL Server schema/import | Unverified; **executed via SSM on app host (C7)** — GitHub runners cannot reach private RDS | `data/unified/sql_server_schema.sql`; `import_sql_server.py:8-11` FK pre-check |
| FDE_VIEWS (7 views) + EXECUTED grants + verification query | Unverified; **moved into initial deployment (C5)** | `scripts/setup_unified_views.sql`; `05` §3 |
| Grafana unified dashboard queries | Unverified | `unified-cold-chain.json:30,52,74,96,118,140` |
| Incident tables/views + append-only attempt model (C9, R3-2) | Tables/views unverified; attempt model planned Phase 4 | `scripts/setup_telemetry.sql`; `03` §B3-B4 |
| Dataset synthetic labeling | Locally verified | `data/unified/README.md`; UI captions |

## C. Event pipeline (Floci → AWS)

| Feature | Status | Evidence |
|---|---|---|
| Event validation/classification | Locally verified | `tests/test_telemetry.py` (4.0 boundary, invalid source/ts/NaN) |
| Worker batch processing + retry (Class A) | Locally verified (stubs) | `tests/test_telemetry.py:39-44`; `worker.py:36-37` |
| SQL idempotent persistence | Locally verified (fake engine) | `tests/test_telemetry_store.py`; `store.py:23-39` |
| **Event→shipment linkage validation (C4)** | Planned Phase 4 (ingest check + validation view) | `03` §B1; today only a convention (`README.md:33`) |
| Floci local pipeline end-to-end | Unverified | requires Docker run (withheld — provisioning) |
| **Post-commit publish loss (Class B)** | Not implemented — **append-only attempt model + reconcile planned Phase 4 (R3-2)**; writer stays INSERT-only (no UPDATE permission, ever) | `worker.py:29-33` duplicate-suppression trap; `03` §B3-B4 |
| SNS publish-ack vs recipient delivery | **Distinct in design (R3-2)**: ledger records `published_acked` only; delivery observed via topic CW metrics + failure alarm | `03` §B4 |
| SQS DLQ + redrive | Planned Phase 4 | absent today (search verified) |
| Agent visibility of incidents | Not possible today — allowlist extension Phase 5 | `sql_guard.py:5` |
| SNS human recipient | Planned Phase 4 (one email) | audit doc:22 |

## D. Chat / SOP / monitoring

| Feature | Status | Evidence |
|---|---|---|
| LibreChat custom endpoint config | Implemented (config only) | `integrations/librechat/librechat.yaml` |
| LibreChat + MongoDB services | Unverified — external, not in compose | `docs/ARCHITECTURE_AUDIT.md:21` |
| Streamlit↔LibreChat/Grafana iframes + full web access design | Designed (C15, `01` §7); unverified live (CSP/cookies/auth against real services) | `src/ui.py:77-81`; `EMBED_IN_STREAMLIT.md` |
| SOP ingestion pipeline | Unverified; configurable workflow designed (C11) | `scripts/ingest_sop_pinecone.py`; `06` §3 |
| SOP metadata (version/date/applicability) | Planned Phase 6 | `06` §4 |
| Weather tool | Unverified (external API); doubles as Phase 0 live tool-call probe | `src/agent_tools.py:122-146` |
| Grafana service + dedicated read-only user | Unverified; USR_GRAFANA_RO from initial deployment | `docker-compose.observability.yml`; `mssql.yml:8-10` (G-SEC-2) |
| CloudWatch observability + NIM token metrics (`ColdChain/NimTokens`, renamed by R6-1) | Planned | `docs/NVIDIA_NIM_RUNTIME.md`; `09` Phase 3/7 |
| Audit log (minimized, C10) | Not implemented — designed `05` §5, Phase 5 | search: no AgentAuditLog |
| Secrets runtime no-leak grep | Planned as Phase 3 acceptance gate | `01` §5.6 |

## E. Deployment / infra

| Feature | Status | Evidence |
|---|---|---|
| GitHub validate workflow | Implemented + runs (Linux) | `.github/workflows/deploy.yml:1-16` |
| Separated build/infra/release pipelines (C6) | Planned | `01` §1 |
| Terraform IaC + S3-native state locking (C2) | Planned; **backend feature itself externally documented only — NOT exercised** (requires Terraform ≥ 1.10 in CI; lock behavior verified in Phase 1 acceptance) | HashiCorp S3 backend docs (URLs in `12_AUDIT_CORRECTIONS.md`) |
| Network topology (C1) | Designed with **exact SG/route rules + external port-scan acceptance (R3-4)**; unverified in a real VPC | `01` §3.1 |
| EC2 host capacity budget + limits + upgrade alarms (R3-6) | Designed — steady ≈4.5/8 GiB, limits 5.9 GiB, MongoDB cache cap, CPUCredit/memory/OOM alarms; verified by `docker stats` drill at Phase 3 | `01` §9 |
| Cognito IdP for ALB auth (R3-5, corrected R4-1) | Designed — user pool; app client **with `generate_secret = true`**, authorization-code grant, scopes + callback; secret confined to Cognito + Terraform state (SSE-KMS, apply-role-only, `sensitive`, no-leak grep); fail-closed rule; admin-provisioned accounts. Unverified live (Phase 3 browser drill + IdP-outage simulation + no-leak grep incl. client secret) | `01` §7.1a; ADR-15 |
| Operator authorization (R4-2) | Designed posture: **pool membership = authorization** — all authenticated pool users may access Streamlit; **no group enforcement claimed**; ALB auth ≠ group authorization; application-side JWT group check designed only as future work | `01` §7.1a; `05` §2 G-SEC-7 |
| Reconciliation mechanics (R4-3, refined R5-1/R5-2) | Designed — single scheduled reconciler + `sp_getapplock`; bounded retries (5/24 h, backoff, exhaustion alarm); `SnsMessageId` on ack; **SNS Standard retained (FIFO not email-compatible)**; **at-least-once semantics: duplicate emails and missing attempt records possible in the ack→insert crash window; database-only reconciliation does not detect every duplicate — topic CW metrics are the cross-check**. Unverified until Phase 4 drills | `03` §B5; ADR-7 |
| Capacity load test + pricing calculator (R4-4) | **Verification gates, not claims**: t3.large adequacy declared only from the Phase 3 measured load test; provisioning requires a current AWS Pricing Calculator export for the selected region (Phase 1 acceptance) and Phase 7 bill reconciliation | `01` §9; `07` header, §6 |
| Deployment sequencing (R3-1) | Designed — host provisioned Phase 2 **before** import; no circular dependency | `09` Phase 2, ADR-16 |
| RDS multiple-databases statement (R3-3) | Corrected in documentation; single-DB design remains a simplicity choice | `07` §2 |
| Cost operating modes 24/7 vs dev-hours (R3-7) | Designed with explicit "bills while stopped" table; validated against first actual bill (Phase 7) | `07` §6 |
| RDS edition/licensing claims (C3) | **Re-verified against AWS docs URLs only — no live account/pricing-console confirmation**; pricing re-validated in Phase 1 acceptance | `07` §2 |
| Per-phase observability co-deployment (R3-8) | Designed — every service's alarms/controls ship in its own phase; Phase 7 is drills + consolidation only | `09` ADR-18 |
| OIDC GitHub→AWS | Planned | — |
| ALB/ACM/OIDC web access | Planned | `01` §7 |

## F. The honesty ledger (features that LOOK done but are not)

1. Grafana dashboards + datasource YAML exist but have never rendered against
   a live MSSQL.
2. `librechat.yaml` exists but LibreChat + MongoDB have never been pointed at
   the gateway.
3. `ingestion_hash_cache.json` proves the SOP script once ran, not that the
   index currently matches the file.
4. The grants in the `.sql` files are comments, not applied permissions —
   until Phase 2 executes them, nothing enforces the matrix.
5. `deploy.yml` is green CI, not deployment.
6. SSE "streaming" is a single delayed chunk, not token streaming.
7. Ollama "usage monitoring" does not exist anywhere in code today — it is
   planned (C12), not configured.
8. The blueprint's corrected networking/state/licensing claims are verified
   against official documentation only — none has been exercised in an AWS
   account; each is re-verified at its phase's acceptance gate.