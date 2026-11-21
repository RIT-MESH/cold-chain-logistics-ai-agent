# 05 — Security Model (requirement 6)

Revision 4 (2026-11-03: operator authorization posture clarified and Cognito
client-secret state security added — R4-1/R4-2). Prior corrections preserved:
incident writer INSERT-only via append-only attempts R3-2; DB least-privilege
in initial deployment C5; audit-log minimization C10; secrets runtime
retrieval C8.

## 1. Current security posture (evidence-based)

| Layer | Control | Evidence | Status |
|---|---|---|---|
| Agent SQL | AST allowlist: single SELECT, single FDE_VIEWS view, TOP 1–100, no joins/CTEs/subqueries/SELECT *, no mutations, 6000-char cap | `src/sql_guard.py:7-41` | ✅ threat-tested (`tests/test_sql_guard.py`) |
| Gateway auth | Bearer `FDE_API_KEY`, constant-time compare, 503 if unconfigured | `api/main.py:40-46` | ✅ tested |
| Prompt injection (system) | Inbound system/developer/tool roles dropped; server-owned prompt | `api/main.py:59-72` | ✅ tested |
| DoS bounds | 60 msgs / 60k chars / recursion 20 / TOP≤100 / fetchmany(10) | `api/main.py:34,97,81`; `agent_tools.py:109` | ✅ |
| Iframe hygiene | http only for localhost/127.0.0.1, no creds-in-URL, no fragments | `src/ui.py:25-29` | ✅ |
| Secrets in repo | `.gitignore` excludes `.env*`; no key in YAML (asserted by test) | `.gitignore:1-6`; `tests/test_llm_provider.py:17-18` | ✅ |
| Worker provenance | `source∈{synthetic,real}` + CHECK constraint; temp range −80..80 | `events.py:27-28`; `setup_telemetry.sql:11,25` | ✅ tested |
| AWS safety interlocks | Publishers refuse real AWS without explicit opt-in | `bootstrap_floci.py:7-8`; `publish_sample_telemetry.py:11-12` | ✅ |

## 2. Findings requiring remediation (gaps)

1. **G-SEC-1 — Grant scripts are documentation, not state.** All GRANTs are
   comments; nothing validates they were applied. Correction C5 moves them
   into the initial database deployment as executed statements + a
   permission-verification query (§3).
2. **G-SEC-2 — Grafana uses the agent's SQL identity** (`mssql.yml:8-10`,
   `docker-compose.observability.yml:13-14`). Fix: `USR_GRAFANA_RO` on
   FDE_VIEWS only, in the initial deployment.
3. **G-SEC-3 — Streamlit MSSQL mode reads base tables** (`src/ui.py:22-24`).
   Correction C5: the public-facing application **never** receives base-table
   grants — the UI is migrated to FDE_VIEWS as a required pre-deployment
   application change (roadmap Phase 2).
4. **G-SEC-5 — No immutable audit log** (TDD §3.3). Design in §5 (C10).
5. **G-SEC-4 — TLS boundaries** — now fully specified in
   `01_AWS_ARCHITECTURE.md` §7 (ALB/ACM/HSTS/host-routing/cookies).
6. **G-SEC-7 — Authorization vs authentication (R4-2):** ALB
   `authenticate-cognito` proves identity; it does **not** enforce group
   membership. Test-env posture: **pool membership = authorization** (the
   admin-provisioned operator pool is the whole access set; every
   authenticated pool user may access Streamlit). No group-based
   authorization is claimed. If required later, enforcement is an
   application-side verification of the ALB-signed `x-amzn-oidc-data` JWT
   (`cognito:groups` claim) — designed as future work, never attributed to
   the ALB (`01` §7.1a).
7. **G-SEC-8 — Cognito client secret in Terraform state (R4-1):** the app
   client generates a secret (`generate_secret = true`), which therefore
   exists in Cognito and in **Terraform state**. Controls: state bucket
   SSE-KMS with a dedicated CMK; state-object IAM restricted to the
   infra-apply role only; `sensitive = true` masking in plan output; the
   secret is never passed to the ALB listener (Cognito holds it
   server-side), containers, images, user data, or logs; the Phase 3
   no-leak grep covers its value; rotation is a documented admin runbook.

## 3. Database least privilege — from the initial deployment (correction C5)

**Principle: views and per-service permissions are part of the database
bootstrap, not a later hardening phase. No application identity ever holds
SELECT on `unified.*` base tables.**

Deployment sequence (roadmap Phase 2, executed via SSM on the app host —
`01_AWS_ARCHITECTURE.md` §4 `sql-init`):

1. Apply `data/unified/sql_server_schema.sql` (tables) — admin plane.
2. Apply `scripts/setup_unified_views.sql` (FDE_VIEWS VW_SHIPMENTS … VW_ROUTES).
3. Apply `scripts/setup_telemetry.sql` (event/incident tables + views).
4. **Execute** (no longer comments) the identity and grant block:
   - `USR_FDE_RO` (agent): `GRANT SELECT ON SCHEMA::FDE_VIEWS`.
   - `USR_COLDCHAIN_UI_RO` (Streamlit): `GRANT SELECT ON SCHEMA::FDE_VIEWS`
     — **views only, never `unified` base tables** (correction C5).
   - `USR_GRAFANA_RO`: `GRANT SELECT ON SCHEMA::FDE_VIEWS` (fixes G-SEC-2).
   - `USR_INCIDENT_WRITER`: `GRANT SELECT, INSERT` on
     `dbo.TelemetryEvents`, `dbo.TelemetryIncidents`,
     `dbo.IncidentNotificationAttempts` only — **no UPDATE/DELETE**
     (R3-2: the append-only attempt model makes UPDATE unnecessary; delivery
     state is derived by query, never mutated).
   - `USR_AUDIT_WRITER` (Phase 5): INSERT-only on `dbo.AgentAuditLog`.
5. Import data (`scripts/import_unified.py`) under the temporary admin
   credential; that secret is deleted after import. The import job is an
   admin-plane action, never an application identity.
6. **Verify**: `sql-init/verify_grants.sql` queries
   `sys.database_permissions` and asserts the exact expected grant set and
   the absence of any application grant on `unified.*` — fails the phase if
   anything is broader than the matrix below.

| Identity | SELECT | INSERT | Where | Used by |
|---|---|---|---|---|
| USR_FDE_RO | ✅ FDE_VIEWS schema | ❌ | — | LangGraph `query_telemetry_db` (`agent_tools.py:44-47`) |
| USR_COLDCHAIN_UI_RO | ✅ FDE_VIEWS schema | ❌ | — | Streamlit MSSQL mode (`ui.py:17`) |
| USR_GRAFANA_RO | ✅ FDE_VIEWS schema | ❌ | — | Grafana datasource (replaces shared agent user) |
| USR_INCIDENT_WRITER | ✅ TelemetryEvents, TelemetryIncidents, IncidentNotificationAttempts | ✅ same three (append-only attempts, R3-2) | — | telemetry worker + reconcile (`store.py:11`) |
| USR_AUDIT_WRITER | ❌ | ✅ AgentAuditLog only | — | gateway (Phase 5) |
| admin (temp) | all | all | deleted after import | import + grants |

Consequence (accepted, planned): the UI's ~8 MSSQL query sites must be
adapted to views (datetime handling `YEAR()`/CAST since views TRY_CONVERT to
datetime2) **before** the first AWS UI deployment — roadmap Phase 2
application change, acceptance-tested in Phase 3. The UI keeps its SQLite
offline mode untouched (ADR-12).

Why per-consumer identities: attribution (which service ran which query),
independent revocation, and blast-radius control — consistent with
guidelines §5.3's "least privilege… narrow grants".

## 4. Secrets management (correction C8 — summary; full design `01_AWS_ARCHITECTURE.md` §5)

- Terraform creates **empty secret containers only**; values are seeded by
  an operator via CLI — no *application* secret value ever enters Terraform
  state. **One documented exception (R4-1/G-SEC-8): the Cognito app-client
  secret, generated by AWS (`generate_secret = true`), necessarily resides
  in Terraform state — protected by SSE-KMS, apply-role-only state IAM,
  `sensitive` masking, the no-leak grep, and a rotation runbook
  (`01` §7.1a).**
- EC2 user data carries zero credentials; the instance profile fetches
  secret values at runtime into **tmpfs** (`/run/coldchain`, mode 0600),
  consumed by containers at start; nothing in image layers or EBS.
- Log scrubber drops `Authorization`/`PWD=`/secret-shaped values before
  CloudWatch; SSM documents marked sensitive.
- Post-deploy smoke greps instance metadata, `docker history`, and CW logs
  for each secret's exact value — any hit fails the gate.
- Rotation: RDS-managed rotation for master only; the four service users are
  rotated via DBA runbook; NVIDIA/Pinecone keys are external-provider
  secrets, rotated manually at the provider.

## 5. Audit log — data-minimized design (correction C10)

TDD §3.3 asked for `{Timestamp, User_Prompt, Tool_Invoked, Tool_Raw_Output,
LLM_Decision}`. Correction C10 narrows retention to what governance needs
without warehousing raw prompts, full tool outputs, credentials, or
sensitive content:

| Field | Retained | Rationale |
|---|---|---|
| `EventId, OccurredAtUtc, SessionId, DurationMs, Model, Provider` | yes | usage/attribution; `Provider` distinguishes cloud vs test |
| `UserPromptHash` | SHA-256 only | correlation without storing raw prompts |
| `UserPromptPreview` | first 120 chars, scrubbed | operator triage; no secrets/PII (scrubber §4) |
| `ToolName` | yes (`query_telemetry_db` / `search_compliance_sop` / `fetch_corridor_conditions`) | TDD "tool_invoked" |
| `ToolQueryHash, ToolViewName, ResultRows, ToolStatus` | hash + metadata only | **raw SQL text and raw tool output are NOT retained** — a hash proves which query ran; view name + row count + status suffice for incident review |
| `LLMDecisionClass` | enum (`answered`, `clarified`, `tool_loop`, `refused`) | TDD "llm_decision" without storing the full answer (answers may echo telemetry/PII) |
| `ErrorClass` | enum/short string | never raw exception text with parameters |
| **Never stored** | full prompts, full responses, raw tool outputs, SQL parameter values, credentials, request headers | explicit non-goals |

Implementation notes (Phase 5): append-only `dbo.AgentAuditLog`, INSERT-only
grant (`USR_AUDIT_WRITER`), redaction scrubber applied *before* insert,
90-day retention via scheduled purge job (append-only for 90 days, then
delete — documented exception), row-size caps, and a test asserting a
synthetic prompt containing a fake `NVIDIA_API_KEY` value lands only as
hash+scrubbed preview. This satisfies the governance intent (decision
traceability per TDD §3.3 / business deck) while honoring the correction's
minimization requirement.

## 6. Network boundaries

See `01_AWS_ARCHITECTURE.md` §3 (chosen topology) and §7 (web access):
public-edge app tier SG-isolated behind ALB, private data tier, port 1433
never public, no SSH, SSM-only administration. TDD §4.2 satisfied.

## 7. Threat-model checks to extend before shipping (per guidelines §7)

- SQL guard: incident-view allowlist cases + `sqlglot` parse fuzzing
  (comments, `EXEC`, stacked statements) — `tests/test_sql_guard.py`.
- Gateway: oversized system/developer histories, 60-message boundary,
  duplicate auth headers; audit-row presence per request; scrubber test (§5).
- Worker: linkage-rejection path (C4), DLQ redrive drill, duplicate event ID,
  append-only attempt rows — including a test that the writer identity
  cannot UPDATE/DELETE (R3-2), SNS-ack vs delivery metrics (B4).
- Iframe: `X-Frame-Options`/CSP regression check on embed pages
  (`EMBED_IN_STREAMLIT.md` guidance).
- Secrets: post-deploy no-leak grep (§4).