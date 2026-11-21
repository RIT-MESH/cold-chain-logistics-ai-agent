# 10 — Architecture Decision Records

Revision 3 (2026-11-03, final corrections). Format: Context → Decision →
Consequences → Status. IDs referenced from other blueprint docs.

## ADR-1 — Compute: EC2 with Docker Compose now; ECS as later migration
Context: audit doc fixes "EC2 initially (containerization and ECS migration
are future work)"; 5–6 containers, demo/test workload.
Decision: single general-purpose EC2 instance running one compose file;
images tagged `sha-<git>`; no orchestration layer yet.
Consequences: simplest deploy/rollback; manual scaling; ECS migration is a
later isolated decision. Status: approved direction (audit), design here.

## ADR-2 — LLM: Ollama Cloud GLM-5.3, SaaS call, no GPU, no local fallback
Context: Ollama Max subscription; guidelines §6 and `src/orchestrator.py`
forbid silent fallback; no GPU infrastructure allowed.
Decision: `LLM_PROVIDER=OLLAMA_CLOUD` → `https://ollama.com/v1`, `glm-5.3`;
key fetched at runtime from Secrets Manager tmpfs; hard failure on missing
key/quota; live verification gated early (Phase 0, C13).
Consequences: zero inference cost in AWS; availability depends on Ollama;
VS Code agent shares the token balance. **Status: SUPERSEDED by operator
decision R6-1 (2026-11-08, `12_AUDIT_CORRECTIONS.md` §Revision 6): NVIDIA NIM
is the only supported provider (`z-ai/glm-5.3-flash` production model per
R6-2); the SaaS-call / no-GPU / no-fallback / Secrets-Manager-tmpfs shape of
this decision carries over unchanged — only the provider and endpoint
changed. Phase 0 C13 gate CLOSED 2026-11-15 (ledger R6-3).**

## ADR-3 — Database: RDS SQL Server, Express first, Standard as internal-app upgrade (corrected C3)
Context: SQL Server fixed (guidelines §4); dataset ≈ 25 MB; edition
licensing re-verified against current AWS/Microsoft documentation.
Decision: RDS (not EC2-installed SQL). **Express** (free license) for the
dev/test tier — it IS licensed for internal applications (earlier draft was
wrong); 10 GB/db cap is irrelevant at our size. **Web edition is excluded**
— Microsoft restricts it to public/internet-facing web workloads, which an
internal operator tool is not (earlier draft wrongly recommended it).
**Standard** is the licensed path if/when the internal app must exceed
Express constraints (Multi-AZ, larger classes). A single `ColdChainLogistics`
database is a simplicity choice — **AWS supports multiple user databases
per RDS SQL Server instance, subject to edition/class/deployment limits**
(R3-3 corrects the earlier one-database claim).
Consequences: edition choice is a licensing decision; costs re-validated in
`07`. Status: planned (Phase 2); pricing/edition table externally verified
against AWS docs URLs in `12_AUDIT_CORRECTIONS.md` but NOT against a live
account.

## ADR-4 — Per-consumer database identities, ALL on views, from initial deployment (corrected C5)
Context: requirement 6 + audit correction: do not grant broad base-table
access to the public-facing application at any point, even temporarily.
Decision: five identities (agent / UI / Grafana / incident-writer /
audit-writer) + temporary import admin; views + EXECUTED grants +
`verify_grants.sql` are part of initial database deployment (Phase 2); the
UI is migrated onto FDE_VIEWS before it is ever publicly deployed; no
application identity ever holds `unified.*` base-table grants.
Consequences: UI query sites need a one-time migration; attribution and
revocation per consumer; grant drift is caught at deploy time.
Status: planned (Phases 2–3).

## ADR-5 — Secrets: empty containers in Terraform, operator-seeded values, runtime tmpfs fetch (corrected C8)
Context: audit correction — no secret values in EC2 user data, Terraform
state, image layers, or logs.
Decision: Terraform creates secret metadata only; values seeded out-of-band
via CLI (so no value ever enters state); instance profile fetches at runtime
into tmpfs (0600) consumed by containers; scrubber before CloudWatch;
post-deploy no-leak grep as a gate.
Consequences: one extra operator step; rotation for service users is
runbook-driven; break-glass audited. Status: planned (Phases 1–3).

## ADR-6 — Delivery pipeline: four separated workflows with manual gates (corrected C6)
Context: audit correction — separate image builds from infra changes from
app releases; manual approval for infrastructure.
Decision: `deploy.yml` (validate, unchanged) + `build-images.yml` (push
sha-tags, no deploy rights) + `infra.yml` (plan on PR; apply via dispatch +
`infra-approval`) + `release.yml` (dispatch with tag + `release-approval`).
Consequences: infra and app move on independent cadences; rollback of app =
previous tag; no single workflow can both mutate infra and release apps.
Status: planned (Phase 1).

## ADR-7 — SNS reliability: DLQ for Class A + append-only attempts for Class B (corrected C9 + R3-2)
Context: `worker.py:29-33` and Floci README:19 acknowledge the commit→publish
gap; a DLQ alone cannot cover failures after commit (redelivery sees a
duplicate and skips publish). The automated writer must have **no UPDATE
permission** (guidelines and `setup_telemetry.sql:46`), so a ledger that
mutates status/counters is inadmissible.
Decision: two failure classes handled separately. Class A (pre-commit):
SQS DLQ + redrive + depth alarm. Class B (post-commit): an **append-only**
`dbo.IncidentNotificationAttempts` table — one INSERTed row per publish
outcome (`published_acked` / `publish_failed`), delivery state **derived by
query**, scheduled reconcile re-publishes incidents with no acked attempt.
An attempt row records an **SNS publish acknowledgement, never recipient
delivery** — recipient delivery is observed only via the topic's CloudWatch
metrics (`NumberOfNotificationsDelivered/Failed/Pending`) with their own
alarm (B4). A full transactional-outbox eventing system remains deferred
until alerts are operationally load-bearing.
Consequences: INSERT-only permission preserved; the silent-loss window
shrinks to the reconcile interval; alert delivery is still a demo (one email
recipient). Reconciliation mechanics fixed by R4-3 (`03` §B5): single
scheduled reconciler by construction + `sp_getapplock` advisory lock;
bounded retries (5 attempts / 24 h, exponential backoff, exhaustion alarm);
`SnsMessageId` stored on ack for metric cross-reference. **R5-2: the
notification path is honestly at-least-once** — a crash between SNS
acknowledgement and the attempt-row insert can leave a published message
with no attempt record, so duplicate emails and missing attempt records are
possible and database-only reconciliation cannot detect every duplicate
(topic CW metrics are the cross-check). **R5-1: SNS Standard is retained;
SNS FIFO is not a drop-in remedy because FIFO topics do not support direct
email subscriptions** — a future FIFO pipeline requires an SQS FIFO consumer
plus a separate email delivery mechanism (deferred). Status: planned
(Phase 4); accepted residual risk documented.

## ADR-8 — Embeddings: configurable, resource-documented, no AWS standing compute (corrected C11)
Context: BGE-M3 ≈ 2.3 GB weights, torch + sentence-transformers, multi-GB
RAM on CPU — too heavy to host for a one-file SOP corpus; OpenAI embeddings
billed separately from Ollama Max; audit asks for a configurable,
cost-conscious workflow.
Decision: `SOP_EMBEDDING_PROVIDER=OPENAI|LOCAL` (default LOCAL on the
developer laptop); optional run-to-completion EC2 spot job (small instance,
minutes, terminated) for laptop-less operation; knobs for chunk size/overlap,
k, and batch; dimension/index alignment rules kept with self-heal.
Consequences: no standing embedding compute in AWS; SOP updates are manual
but cheap; OPENAI option is an explicit extra bill. Status: implemented
(script knobs planned Phase 6); unverified live.

## ADR-9 — Agent SQL surface: AST allowlist + views only, extended test-first
Context: guidelines §5.3 "never weaken guards to make a sample query work".
Decision: extend `ALLOWED_VIEWS` with the two incident views in Phase 5 with
`tests/test_sql_guard.py` cases in the same commit; agent contract stays
single-view, TOP ≤ 100. Consequences: agent can answer incident questions;
guard complexity grows one step. Status: planned.

## ADR-10 — Audit log: append-only SQL table, INSERT-only identity, data-minimized (corrected C10)
Context: TDD §3.3 wants {Timestamp, User_Prompt, Tool_Invoked,
Tool_Raw_Output, LLM_Decision}; audit correction forbids retaining raw
prompts, full tool outputs, credentials, sensitive data.
Decision: `dbo.AgentAuditLog` storing hashes + 120-char scrubbed prompt
previews + tool name/view/row-count/status + decision class — never raw
prompts, raw SQL text, raw tool outputs, or secrets; INSERT-only grant;
90-day purge; scrubber test with a planted fake key.
Consequences: satisfies decision-traceability governance without warehousing
sensitive content; forensic depth is deliberately limited (hashes, not
contents). Status: planned (Phase 5).

## ADR-11 — Streamlit↔LibreChat↔Grafana remain iframed, separately authenticated
Context: guidelines §5.1: preserve both; embedding doesn't bypass auth.
Decision: host-routed subdomains under one registrable domain; ALB
`authenticate-cognito` gates Streamlit (no built-in auth); LibreChat/Grafana keep own logins; CSP
frame-ancestors exact-origin per service; cookies SameSite=Lax+Secure
(same-site subdomains avoid third-party-cookie blocking).
Consequences: cross-domain deployments unsupported by design; SSO is an
explicit future project. Status: designed (`01` §7); unverified live.

## ADR-12 — Offline-first survival guarantee
Context: repo must remain runnable with zero cloud.
Decision: every phase keeps the SQLite demo path working; "destroy infra"
always leaves a functioning local demo; canonical dataset files are never
forked for AWS. Consequences: no drift between local and deployed data
definitions. Status: current invariant, preserved.

## ADR-13 — Network: SG-isolated public-edge app tier + private data tier (corrected C1)
Context: audit found the previous blueprint contradicting itself (private
subnets vs public subnets vs no public IP).
Decision: app host in public subnets WITH a public IPv4 (needed for
IGW egress to integrate.api.nvidia.com/pinecone.io/open-meteo.com/registries/AWS APIs
without NAT), SG inbound ONLY from the ALB SG, no SSH, SSM-only admin; RDS
in no-IGW data subnets, `publicly_accessible=false`, 1433 source=app SG.
The documented alternative (private app tier + NAT + endpoints, +$40–60/mo)
is a future posture swap, not the current design.
Consequences: public IPv4 charges (~$3.6/mo each); simpler, cheaper, and
fully sufficient for a demo; defense rests on SGs + no inbound exposure.
Status: designed (Phase 1); unverified in a real VPC.

## ADR-14 — Terraform state: S3 native lockfile, no DynamoDB (corrected C2)
Context: DynamoDB state locking is deprecated; S3 backend supports native
locking.
Decision: `use_lockfile = true` (Terraform ≥ 1.10 pinned in CI); versioning
+ SSE-KMS on the state bucket; separate keys and IAM boundaries per
environment; state never used as a rollback mechanism (C14 doctrine).
Consequences: one less stateful service; requires a recent Terraform pin.
Status: planned (Phase 1); feature externally verified against HashiCorp
docs only — NOT yet exercised in this account.

## ADR-15 — Web access: one registrable domain, ALB TLS, Cognito-gated shell (corrected C15, R3-5, R4-1/R4-2)
Context: full browser-facing design required (C15/R3-5); the Revision 4
audit corrected the Cognito client-secret claim (the app client **must
generate a secret** — the earlier "secret-less client" statement was wrong)
and demanded an honest authorization posture.
Decision: single zone with subdomains (app/chat/grafana); one ACM cert;
ALB TLS13 policy + HSTS + 80→443 redirect; gateway not publicly routed
(compose-network only); **Amazon Cognito user pool** as the ALB
`authenticate-cognito` IdP: app client with **`generate_secret = true`**,
**authorization code grant only**, scopes `openid email`, callback
`…/oauth2/idpresponse`; the generated secret exists only in Cognito and
Terraform state (SSE-KMS, apply-role-only IAM, `sensitive`, covered by the
no-leak grep; rotation runbook); the ALB listener never receives the secret
as a parameter; **fail-closed on IdP outage** preserved. Authorization
(R4-2): ALB authenticates identity only — **pool membership = authorization**
for the test environment; no group enforcement is claimed; group-based
authorization, if ever required, is application-side verification of the
ALB-signed `x-amzn-oidc-data` JWT — explicit future work.
Consequences: domain ownership external prerequisite (U-4); the client
secret in state is a protected sensitive artifact; Cognito onboarding policy
(U-8) governs who is in the pool.
Status: designed (`01` §7.1a); unverified live (Phase 3 browser drill +
IdP-outage simulation + no-leak grep incl. the client secret).

## ADR-16 — Import/administration runs on the app host via SSM (corrected C7 + R3-1)
Context: GitHub-hosted runners cannot reach private RDS; Revision 2
sequenced the import (Phase 2) before the host it needed (Phase 3) — a
contradiction caught by the Revision 3 audit.
Decision: **the EC2 host is provisioned in Phase 2, before any import or
grant execution.** All data-plane admin (schema, grants, import, reconcile)
executes on that host through SSM RunCommand/Session with runtime-fetched
admin credentials; runners drive only the control plane. Phase 3 adds web
access and application containers to the already-running host — no
temporary import runner is created (a throwaway VPC runner was considered
and rejected: duplicate host management, IAM surface, and cost for zero
benefit).
Consequences: Phase 2's acceptance includes host-reachability (SSM yes,
internet no); Phase 3 has no infra dependency on import steps.
Status: planned (Phase 2).

## ADR-17 — App host capacity budget with enforced limits and upgrade thresholds (R3-6; R4-4)
Context: one t3.large hosts six containers; LibreChat, MongoDB, and the
agent are memory-significant; MongoDB defaults to ~50% host RAM for its
cache; LOCAL BGE-M3 query-time embeddings would add ~3 GB to the gateway.
Decision: published per-container memory/CPU budget with enforced compose
limits (`01` §9: gateway 1.5 GB, streamlit 1.5 GB, librechat 1 GB, mongodb
1 GB with cache capped at 0.5 GB, grafana 512 MB, worker 384 MB; steady
≈4.5 GB of 8 GiB); swapfile 2 GB; CloudWatch alarms for CPU-credit drain,
memory >75%, and container OOM-kill; upgrade path t3.large → m5.large
(CPU-bound) or r5.large (memory-bound / LOCAL-embedding posture U-9).
**R4-4: t3.large is a test candidate — capacity is declared adequate only
after the measured load test at Phase 3 acceptance.**
Consequences: predictable density; the SOP-retrieval embedding posture
(U-9) must be decided before sizing is final. Status: designed; thresholds
deploy with the host in Phase 2 (R3-8), verified by `docker stats` drill
plus the Phase 3 load test.

## ADR-18 — Observability and security controls ship with their service (R3-8)
Context: Revision 2 grouped alarms in Phase 7, leaving services temporarily
unmonitored and the audit asking for co-deployment.
Decision: each phase deploys the alarms and security checks for the
services it creates — Phase 1 billing alarm; Phase 2 host + RDS alarms and
the external port-scan gate; Phase 3 gateway alarms, token metrics, secrets
no-leak grep, Cognito drill; Phase 4 DLQ/reconcile/SNS-delivery alarms;
Phase 5 audit-log growth alarm; Phase 7 only consolidates dashboards and
runs fault drills.
Consequences: no service exists without its monitoring; Phase 7 acceptance
is a drill set, not a deployment catch-up. Status: planned (roadmap
restructured `09`).