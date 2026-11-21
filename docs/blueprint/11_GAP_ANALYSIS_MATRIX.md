# 11 — Consolidated Gap Analysis Matrix

Revision 4 (2026-11-03: Cognito client-secret correction R4-1,
authorization posture R4-2, reconciliation mechanics R4-3, verification
gates preserved R4-4, cross-document consistency R4-5). Prior corrections
C1–C15 and R3-1–R3-10 remain in effect; corrections ledger in
`12_AUDIT_CORRECTIONS.md`. Severity: **Blocker** · **High** · **Medium** ·
**Low** · **Accepted**.

## A. Pre-existing gaps (revision 1, re-triaged)

| ID | Area | Gap | Severity | Evidence | Fix (phase) |
|---|---|---|---|---|---|
| G-AW-1 | AWS architecture | No Terraform/IaC; deploy.yml validates only | Blocker | zero TF files (search); `deploy.yml:17` | Phase 1 |
| G-AW-2 | AWS architecture | RDS/import/grants never executed live | Blocker | `.sql` grant comments | Phase 2 (SSM import, C7) |
| G-AW-3 | AWS architecture | LibreChat + MongoDB not shipped | High | audit doc:21 | Phase 3 |
| G-AW-4 | AWS architecture | No CloudWatch/observability | Medium | — | Phase 7 |
| G-LL-1 | NVIDIA NIM (was Ollama Cloud — R6-1) | GLM-5.3 Flash tool-calling never verified live | **RESOLVED 2026-11-15** — C13 gate CLOSED (ledger R6-3); live evidence `docs/evidence/C13_LIVE_GATEWAY_VERIFICATION.md` | `docs/evidence/C13_LIVE_GATEWAY_VERIFICATION.md` | ~~Phase 0 gate (C13)~~ done |
| G-LL-2 | NVIDIA NIM (was Ollama Cloud — R6-1) | SSE is delayed whole-answer | Medium (known) | `api/main.py:118-132` | accepted |
| G-LL-3 | NVIDIA NIM (was Ollama Cloud — R6-1) | No usage/credit monitoring | High | absent in code | **Phase 3 (C12 implemented at R6-1: per-request token accounting + `ColdChain/NimTokens` metric); NIM has no credit API (F-4) — metric is the consumption signal** |
| G-DS-1 | Unified dataset | SQL Server parity unverified live | Blocker | import never run | Phase 2 |
| G-DS-2 | Unified dataset | Incidents deliberately not FK'd to unified | Accepted (by design) | `setup_telemetry.sql` | linkage validation instead (C4, Phase 4) |
| G-EP-1 | Events | Post-commit publish loss | **High** (was Accepted) | `worker.py:29-33` | **append-only attempt model + reconcile (C9, R3-2), Phase 4** |
| G-EP-2 | Events | No DLQ/redrive | High | absent | Phase 4 |
| G-EP-3 | Events | Agent cannot query incident views | High | `sql_guard.py:5` | Phase 5 (test-first) |
| G-EP-4 | Events | JSONL fallback duplicates | Medium | Floci README:19 | accepted locally; SQL on AWS |
| G-EP-5 | Events | No human SNS recipient | Medium | audit doc:22 | Phase 4 (one email) |
| G-DI-1 | Dashboards | Grafana/LibreChat/Streamlit-MSSQL never live | High | honesty ledger | Phase 3 |
| G-DI-2 | Dashboards | Default 2026 window; 2021–2026 selectable | Low | dashboard JSON:8-10 | documented |
| G-SEC-1 | Security | Grants are comments, never applied | High | `.sql` files | **Phase 2 executes + verifies (C5)** |
| G-SEC-2 | Security | Grafana shares agent SQL identity | High | `mssql.yml:8-10` | Phase 2/3 (USR_GRAFANA_RO) |
| G-SEC-3 | Security | UI reads base tables (MSSQL mode) | High | `src/ui.py:22-24` | **Phase 2 UI-on-views migration (C5); no app base-table grants ever** |
| G-SEC-4 | Security | No TLS boundary definitions | High → **designed** | TDD 4.2 | **`01` §7 (C15)**; live verification Phase 3 |
| G-SEC-5 | Security | No audit log | High | search: absent | **Phase 5, minimized design (C10)** |
| G-SEC-6 | Security | Single shared FDE_API_KEY, no per-user auth | Medium (documented) | LibreChat README:43 | accepted for test env; ALB `authenticate-cognito` gates Streamlit (`01` §7) |
| G-SOP-1 | SOP | Live ingestion/index state unknown | High | hash cache only | Phase 6 runbook |
| G-SOP-2 | SOP | No version/date/applicability metadata | Medium | `ingest_sop_pinecone.py:210-212` | Phase 6 |
| G-SOP-3 | SOP | Human-review framing unverified live | High | needs G-LL-1 | Phase 0 gate + Phase 6 transcript |
| G-CO-1 | Cost | Prices unvalidated; no billing alarm | Medium | `07` caveats | Phase 1 (alarm + pricing-calculator acceptance step) |
| G-HY-1 | Test hygiene | Windows encoding test failure (1/21) | Medium | reproduced in review | Phase 0 |
| G-HY-2 | Config hygiene | Stale `fleet-history` URL | Low | example file:2 | Phase 0 |
| G-HY-3 | Documentation | Guidelines reference nonexistent `docs/LEGACY_INSTRUCTIONS_UNSAFE.md` | Low | file absent | Phase 0 doc note |
| G-RO-1 | Roadmap | notes.pdf (5 scanned pages) unread — may contain unreflected requirements | Unknown | no text layer, no OCR | **unresolved — see §C** |

## B. Gaps found/fixed by the audit itself (corrections C1–C15)

| ID | Audit issue | Resolution in revision 2 | Status |
|---|---|---|---|
| C1 | Network contradiction (private/public/no-IP) | ONE chosen topology: SG-isolated public-edge app tier + private data tier, no NAT; alternative documented (ADR-13, `01` §3) | **Resolved in design**; unverified in real VPC |
| C2 | DynamoDB state locking deprecated | S3 native `use_lockfile=true`, versioning, SSE-KMS, per-env state keys + IAM boundaries (ADR-14, `01` §2) | Resolved in design; feature externally documented only |
| C3 | RDS licensing/edition claims wrong | Express IS internal-app licensed (chosen dev tier); Web EXCLUDED (public-web-only restriction); Standard upgrade path; supported classes + costs incl. public IPv4, backups, ALB, CloudWatch (`07`) | Resolved against docs URLs; no live pricing confirmation |
| C4 | Event→shipment linkage unspecified | Ingest-time linkage validation + `VW_EVENT_LINK_VALIDATION` + Observed vs Ingested timestamps + visibility in all three UIs (`03` §B1) | Designed; implementation Phase 4 |
| C5 | DB least privilege too late; broad app access | Views + EXECUTED grants + verification query moved to initial DB deployment; UI migrated to views BEFORE public deployment; no app base-table grants ever (`05` §3, ADR-4) | Designed; Phases 2–3 |
| C6 | Pipelines conflated | Four separated workflows (validate/build/infra/release) with manual approval gates (ADR-6, `01` §1) | Designed; Phase 1 |
| C7 | Runner→private-RDS impossible | All data-plane admin via SSM on the app host; runners drive control plane only (ADR-16) | Designed; Phase 2 |
| C8 | Secrets exposure paths | Empty TF secret containers, operator seeding, runtime tmpfs fetch, scrubber, no-leak grep gate (`01` §5, ADR-5) | Designed; Phase 1–3 |
| C9 | DLQ vs post-commit failures conflated | Two-class model; DLQ for Class A; **append-only attempt table + reconcile + age alarm for Class B** (R3-2); SNS-ack ≠ recipient delivery, delivery via topic CW metrics (`03` §B3-B4, ADR-7) | Designed; Phase 4 |
| C10 | Audit log would retain too much | Data-minimized design: hashes/previews/metadata, never raw prompts/outputs/creds; 90-day purge (`05` §5, ADR-10) | Designed; Phase 5 |
| C11 | Embedding resources unaddressed | Resource table (BGE-M3 ≈ 2.3 GB, torch RAM); configurable provider/knobs; optional run-to-completion spot job; no standing compute (`06` §2-3, ADR-8) | Designed; Phase 6 |
| C12 | No LLM usage safeguards (was Ollama — R6-1) | Token accounting from `usage_metadata` **implemented at R6-1** (`api/main.py` C12 counter), CW metric `ColdChain/NimTokens`, alarms + concurrency/budget design (`docs/NVIDIA_NIM_RUNTIME.md`) | Implemented (counter live-verified 2026-11-15); CW metric/alarm wiring Phase 3 |
| C13 | Live LLM verification too late | Phase 0 gate: `check_nim.py` (replaced check_ollama_cloud at R6-1) + live tool-call via weather tool before any infra; SQL tool-call at Phase 3 smoke (`09`) | **CLOSED 2026-11-15 (ledger R6-3)** — evidence `docs/evidence/C13_LIVE_GATEWAY_VERIFICATION.md` |
| C14 | State overwrite as rollback | Explicit rollback doctrine: revert+apply-forward, destroy, PITR, previous image tag; state NEVER edited/overwritten (`01` §8, `09`) | Resolved (documentation) |
| C15 | Web access unspecified | Full ALB/TLS/host-routing/ALB-Cognito/CSP/cookies/same-site-domain design (`01` §7, ADR-15) | Designed; live verification Phase 3 |

## B2. Revision 3 findings (final technical corrections)

| ID | Audit issue | Resolution in revision 3 | Status |
|---|---|---|---|
| R3-1 | Phase 2 imported data via SSM on a host created in Phase 3 (circular) | **Host provisioned in Phase 2 before import** (no temp runner needed; throwaway runner rejected — ADR-16); dependency diagram + acceptance criteria updated | Resolved in design; verification Phase 2 |
| R3-2 | Ledger implied UPDATE permissions the writer must not have; SNS ack ≠ delivery | **Append-only `IncidentNotificationAttempts`** (INSERT-only, state derived by query); ack-vs-delivery distinction documented with topic CW metrics + alarm (`03` §B3-B4, ADR-7, `05` §3) | Resolved in design; verification Phase 4 drill |
| R3-3 | "One database per instance" claim wrong | Corrected: AWS supports multiple databases per instance subject to edition/class/deployment limits; single-DB design kept as simplicity choice (`07` §2) | Resolved (documentation) |
| R3-4 | "Public IPv4" vs "only ALB inbound" contradiction in acceptance | Exact SG/route table + assertion method (describe-security-groups + external port scan; zero open ports on host in Phase 2) (`01` §3.1) | Resolved in design; verification Phase 2/3 |
| R3-5 | ALB IdP unspecified | **Amazon Cognito user pool** — full config (callback, session, fail-closed; **superseded by R4-1: client must `generate_secret = true`; secret state-handling documented**) (`01` §7.1a, ADR-15) | Resolved in design; verification Phase 3 |
| R3-6 | No practical EC2 capacity budget | Per-container steady-state + enforced limits, MongoDB cache cap, swap, CPUCredit/memory/OOM alarms, upgrade thresholds (`01` §9, ADR-17) | Resolved in design; verification Phase 3 `docker stats` |
| R3-7 | Costs not split 24/7 vs dev-hours; stop-billing unclear | Mode A ≈ $135–140 vs Mode B ≈ $90 (≈ $65–75 with RDS stop rule); explicit bills-while-stopped table incl. EBS/ALB/Secrets (`07` §6) | Resolved in design; validated at Phase 7 bill review |
| R3-8 | Observability/security controls deferred to Phase 7 | Co-deployment rule: billing alarm P1; host/RDS alarms + port-scan P2; gateway/token/no-leak/Cognito P3; DLQ/SNS P4; audit alarm P5; Phase 7 = drills only (ADR-18) | Resolved (roadmap restructure) |
| R3-9 | Consistency across all documents | 00/01/03/05/07/08/09/10/11/12 revised together; cross-references re-checked | Resolved (this revision) |
| R3-10 | Revision 3 findings must be recorded | `12_AUDIT_CORRECTIONS.md` §Revision 3 with sources + outstanding gates | Resolved (documentation) |

## B3. Revision 4 findings (final authentication and reliability corrections)

| ID | Audit issue | Resolution in revision 4 | Status |
|---|---|---|---|
| R4-1 | Cognito "secret-less client" claim contradicted AWS docs | **Withdrawn and corrected**: app client with `generate_secret = true`, authorization-code grant, scopes + callback documented; client secret handling specified (Cognito + Terraform state only; SSE-KMS, apply-role-only state IAM, `sensitive`, no-leak grep, rotation runbook; ALB listener never receives the secret). No claim of "no secrets" remains anywhere (`01` §7.1a, ADR-15, `05` G-SEC-8) | Resolved in design; verification Phase 3 (browser drill + no-leak grep incl. secret value) |
| R4-2 | `dispatchers` group mentioned without enforcement | Honest posture: **pool membership = authorization** (all authenticated pool users may access Streamlit); group claim removed as a control; group-based authorization, if required, = application-side `x-amzn-oidc-data` JWT verification — designed future work, never attributed to ALB auth (`01` §7.1a, `05` G-SEC-7) | Resolved in design; posture demonstrated (as designed) at Phase 3 |
| R4-3 | Reconciliation under-specified | Mechanics fixed (`03` §B5): single scheduled reconciler + `sp_getapplock` advisory lock; bounded retries (5 attempts/24 h, exponential backoff, exhaustion alarm); `SnsMessageId` on ack; ack-vs-delivery and guarantees tables; append-only design preserved (INSERT-only, unchanged). **Refined by R5-1/R5-2: SNS Standard retained (FIFO is not email-compatible); duplicates are NOT all database-detectable** | Resolved in design; verification Phase 4 drills |
| R4-4 | Capacity and cost must stay verification-gated | t3.large re-labeled a **test candidate** adequate only after the Phase 3 measured load test; both billing scenarios retained (24/7 vs dev-hours); **current AWS Pricing Calculator export for the selected region = hard precondition before provisioning**, re-checked at Phase 1 acceptance and reconciled against the first bill at Phase 7 (`01` §9, `07` header/§6, ADR-17) | Resolved (gates strengthened); verification Phases 1/3/7 |
| R4-5 | Cross-document consistency | 01/03/05/07/08/09/10/11/12 revised together; stale "secret-less"/"no secret" statements swept; ADR map and gap rows updated | Resolved (this revision) |

## C. Unresolved decisions (require approver input)

1. **notes.pdf content (G-RO-1)** — unread scanned pages; may contain
   requirements not reflected in the blueprint.
2. **AWS region** — costs quoted for us-east-1; another region changes
   numbers and requires a pricing re-estimation step.
3. **RDS tier** — Express (dev/test) vs Standard (licensed internal-app
   upgrade): an explicit licensing/appropriateness decision.
4. **Domain ownership** — the same-registrable-domain rule (ADR-15) requires
   a public DNS zone the approver controls; affects ALB, cookies, CSP.
5. **SNS recipient** — one email subscription needs a named mailbox.
6. ~~**Ollama usage-field confirmation**~~ — **RESOLVED (U-6, R6-1)**: NIM
   returns `usage` fields on every response (live-confirmed 2026-11-03 and in
   the C13 conversation 2026-11-15); C12's token accounting is implemented.
7. **Web edition** — permanently excluded; recorded so the exclusion is a
   decision, not an oversight.
8. **Cognito operator onboarding** (R3-5) — who provisions dispatcher
   accounts, and is MFA required in the test environment?
9. **SOP-retrieval embedding posture on AWS (U-9)** — OPENAI query-time
   embeddings (recommended: no local model in the gateway, small per-query
   bill) vs LOCAL BGE-M3 (requires r5.large per the capacity budget,
   `01` §9). Decided before Phase 6.
10. **Cost operating mode** — 24/7 (≈ $135–140/mo) vs dev-hours
    (≈ $90/mo) + RDS stop discipline (`07` §6).

## D. Requirement coverage cross-check (post-correction)

| Req | Designed? | Remaining gate |
|---|---|---|
| 1 AWS architecture | Yes — corrected (`01`) | approval + account/region/budget |
| 2 NVIDIA NIM runtime (was Ollama Cloud — R6-1/R6-2; `docs/NVIDIA_NIM_RUNTIME.md`) | Yes | ~~Phase 0 live gate (C13)~~ **CLOSED 2026-11-15 (R6-3)** |
| 3 Unified dataset | Yes (`03` §A) | live import (Phase 2) |
| 4 Event processing | Yes — corrected (`03` §B) | Phase 4 drills |
| 5 Dashboards | Yes (`04`) | live rendering (Phase 3) |
| 6 Security | Yes — corrected (`05`) | executed grants (Phase 2), audit log (Phase 5) |
| 7 SOP retrieval | Yes — corrected (`06`) | live ingest (Phase 6) |
| 8 Cost | Yes — corrected (`07`) | pricing validation (Phase 1) |
| 9 Verification | Yes (`08`) + correction ledger (`12`) | n/a |
| 10 Roadmap | Yes — corrected (`09`) | n/a |

## E. Recommended sign-off order (revised, Revision 4)

1. Resolve §C items 1–3 (notes.pdf, region, RDS tier) and 8–10 (Cognito
   onboarding, embedding posture, operating mode) — noting R4-2 makes pool
   membership the authorization, so Cognito onboarding policy (U-8) is a
   genuine access-control decision.
2. Approve ADRs 1–18 (as corrected in Revisions 3–4).
3. Approve Phase 0 start (hygiene + live-LLM gate); subsequent phases
   proceed only per the acceptance criteria in `09_IMPLEMENTATION_ROADMAP.md`
   — with the R4-4 gates (pricing-calculator export before provisioning;
   measured load test before capacity is declared) as hard preconditions.