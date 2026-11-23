# Cold-Chain Logistics AI Agent

A production-grade, evidence-grounded AI agent for cold-chain logistics
operations — answering shipment, telemetry, incident, and SOP questions over
a **guarded, read-only** synthetic SQL Server dataset, with deterministic
offline intelligence, NeMo Guardrails security, and Google-authenticated
live AI (currently gated).

> **Synthetic data notice:** everything the agent answers from is a SYNTHETIC
> TEST DATASET (2021–October 2026). No real shipments, vehicles, or
> measurements are represented. Every answer says so.

**Portfolio:** https://huggingface.co/spaces/ritmesh/cold-chain-logistics-ai

---

## The problem

Cold-chain operators need fast answers to questions like *"any problems with
shipment UC-003916?"*, *"compare it with UC-003241"*, and *"what does the SOP
say to do about it?"* — from telemetry, incidents, custody, alerts, and
procedure documents that live in different systems. Hand-writing SQL or
clicking through dashboards is slow; a plain LLM answer is dangerous. The
agent must: never fabricate a temperature, never execute unvetted SQL, never
invent a procedure citation, and stay useful when the LLM provider is having
a bad day.

## Current architecture

### Offline portfolio (Hugging Face V2 — fully functional)

```
React 18 + TypeScript + Vite + Recharts
  8 interactive views (Dashboard, Shipment Explorer, AI Assistant,
  Incident Center, SOP Knowledge Base, Scenario Simulator,
  Architecture, Engineering Evidence)
  Full dataset: 4,020 shipments, 64,120 telemetry rows
  Deterministic offline AI: no network calls, no backend required
  Dark/light mode, responsive, keyboard-accessible
```

- **Completely AWS-independent** — works without any backend, database or AI service
- Every interactive control works: charts, sorting, comparison, CSV/JSON export
- CSV exports include spreadsheet formula-injection protection
- HTML/script injection prevented (React auto-escaping + input limits)

### Supabase authentication and gateway (deployed, tested)

```
Google OAuth → Supabase Free (PostgreSQL quotas) → Edge Function gateway
  JWT verified on every request (server-side)
  Atomic quota reservation: 10/user/day, 5/hr, 2/min
  Concurrency: 1 per user, 2 global, 100 global/day
  Emergency inference kill switch
  Account suspension enforcement
```

- Google sign-in is **live and working** (only `openid`, `email`, `profile` scopes — no Gmail content access)
- Row-Level Security on all database tables
- Quota functions executable **only by the service role** (not by users)
- SQL migrations hardened: hardcoded limits, daily reset logic, cross-user protection

### NeMo Guardrails security service (locally tested)

```
NVIDIA NeMo Guardrails 0.25.0 (pinned) + FastAPI
  Input rails: jailbreak, SQL injection, secret extraction detection
  Dialog rails: topic-restricted flows
  Tool rails: fixed allowlist, validated arguments
  Retrieval rails: SOP prompt-injection screening
  Output rails: secret redaction, evidence grounding
  Fail-closed: any error → reject (never bypass guardrails)
```

- **18/18 malicious inputs detected (100% detection rate)**
- **14/14 normal questions allowed (0 false positives)**
- **10/10 tool authorization checks correct**
- **8/8 output validation checks correct**
- Rail latency: ~15ms average
- **Zero NVIDIA tokens consumed** (LLM mocked during testing)

### NVIDIA NIM inference (currently disabled)

Live NVIDIA inference requires **all** of the following before activation:
1. NeMo Guardrails deployed to a verified Python hosting runtime
2. NVIDIA public-portfolio terms confirmed
3. End-to-end staging tests passed
4. Explicit operator approval

**No unguarded inference will ever be enabled.** The gateway fails closed when
NeMo Guardrails is unavailable.

---

## Historical AWS implementation (retired 2026-11-23)

The AWS environment served as the original deployment platform from
2026-11-15 to 2026-11-23. The complete infrastructure-as-code, deployment
configuration, observability stack, and Phase 0–8 engineering evidence are
preserved in this repository as historical documentation.

| Component | Status |
|---|---|
| EC2 (t3.large, 9 Docker containers) | **Terminated** |
| RDS SQL Server Express (SQL Server 2022) | **Deleted** (data reproducible from CSVs) |
| Application Load Balancer + CloudFront | **Deleted** |
| Grafana (6 dashboards, 4 datasources) | **Deleted** (dashboard JSON preserved in repo) |
| LibreChat + Cognito authentication | **Deleted** (replaced by Supabase Google OAuth) |
| Secrets Manager (12 secrets) | **Deleted** (credentials preserved in local `.env`) |
| S3, SQS, SNS, CloudWatch | **Deleted** |
| Recurring AWS charges | **~$1/month** (one KMS key, deletion pending) |

The full Terraform source (30 `.tf` files across 8 modules), Docker Compose
(9-service stack), Grafana dashboards (6 JSON), and 18 Phase 0–8 evidence
reports are preserved in this repository. They document a verified,
production-grade AWS deployment that was retired when the architecture
migrated to Hugging Face Static + Supabase Free + NVIDIA NIM.

---

## Key features

- **Conversational shipment intelligence** — reports, contextual follow-ups
  ("any problem in *this* shipment?"), temperature/humidity history, incident
  investigation with notification state, and two-shipment comparisons with
  explicitly stated criteria
- **Hardened SQL access** — allowlisted `FDE_VIEWS` only, read-only identities,
  `TOP`-capped explicit-column SELECTs, full TLS verification, TLS certificate
  verification, never trust-server-cert
- **Deterministic evidence prefetch** — recognized shipment questions pre-run
  fixed guard-valid queries and answer in ONE synthesis turn
- **Intent-based answer validation** — strict ground-truth checks for the
  incident+SOP flow, light checks for history/comparison, honest-unavailability
  rules, one bounded corrective retry, raw tool-call syntax never reaches a user
- **Deterministic fallback** — if synthesis fails, a clearly-labeled
  evidence-only summary is assembled from the same guarded SQL
- **NeMo Guardrails** — 100% malicious-input detection, 0 false positives,
  fail-closed, metadata-only audit logging
- **Geography protection** — synthetic GPS coordinates are reported as recorded;
  place-name labels are never attached without verified evidence
- **Risk honesty** — recorded risk classifications have no documented derivation;
  the agent never invents causes and keeps envelope compliance, risk flags,
  confirmed incidents, and receiver verdict separate

## Repository structure

| Directory | Content | Status |
|---|---|---|
| `src/` | Python AI agent (LangGraph, SQL guard, validators, prefetch, fallback) | Current |
| `api/` | FastAPI OpenAI-compatible gateway | Current |
| `tests/` | Python test suite (382+ tests) | Current |
| `hf-space-v2/` | V2 React portfolio (offline AI, NeMo Guardrails, Supabase) | Current |
| `data/` | Complete synthetic dataset (CSVs + SQLite + import pipeline) | Current |
| `architecture-review/` | NIM model benchmarks and validation harnesses | Historical evidence |
| `docs/guides/` | Reusable LibreChat implementation guide | Current |
| `infra/` | Terraform IaC (30 modules) | **Historical (AWS retired)** |
| `docker/` | Docker Compose deployment (9 services) | **Historical (AWS retired)** |
| `integrations/` | Grafana dashboards + LibreChat configuration | **Historical (AWS retired)** |
| `docs/blueprint/` | Architecture design documents (13) | **Historical** |
| `docs/evidence/` | Phase 0–8 verification reports (18) | **Historical evidence** |
| `hf-space/` | V1 portfolio (superseded by V2) | Historical |
| `scripts/` | Curated tools (ingestion, validation, deployment scripts) | Mixed |

## Running the offline portfolio (V2)

```bash
cd hf-space-v2
npm install                    # install React/Vite dependencies
npm run build                  # production build (requires bundle.json)
npm run dev                    # development server
npx playwright test            # browser test suite (32 tests)
npx vitest run                 # unit test suite
```

The offline portfolio requires **no backend, no database, no API keys, and
no external services**. It is fully functional as a static site.

## Running the Python agent tests

```bash
pip install -r requirements.txt -r requirements-api.txt
pytest -q tests/              # 382+ tests
```

## Running the NeMo Guardrails service

```bash
cd hf-space-v2/security/nemo-guardrails
pip install -r requirements.txt
python nemo_service.py         # HTTP service on :9071
python redteam_tests.py       # adversarial test suite (55 cases)
```

## Running the dataset verification

```bash
cd hf-space-v2
node scripts/verify-dataset.mjs   # integrity checks (64,120 rows verified)
node scripts/build-bundle.mjs      # rebuild bundle.json from CSVs
```

## Test coverage

| Test suite | Tests | Status |
|---|---|---|
| Python agent (pytest) | 382+ | ✅ All passing |
| V2 frontend (Vitest) | 64 | ✅ All passing |
| V2 browser (Playwright) | 32 | ✅ All passing |
| NeMo Guardrails (red-team) | 55 HTTP cases | ✅ 100% detection, 0 FP |
| Dataset integrity (Node) | 20 checks | ✅ All passing |
| Security scan (Gitleaks) | Full history | ✅ 0 findings |
| TypeScript compilation | tsc --noEmit | ✅ 0 errors |
| Production build | vite build | ✅ Succeeds |

## Verified performance (measured, not estimated)

- Model qualification: glm-5.3-flash 14/15 complete-correct (zero API failures)
- Under full LLM-provider degradation: 8/8 acceptance conversation completed
  (5 via validated synthesis, 3 via labeled fallback); shipment questions
  answered in 7–38 seconds while general chat was degraded
- Offline AI: deterministic, instant (no network calls)
- NeMo rail latency: 15ms average (deterministic patterns, no LLM dependency)

## Known limitations

- Live NVIDIA inference is **disabled** pending NeMo hosting deployment +
  terms confirmation + operator approval
- NeMo Guardrails is locally tested but **not deployed to a production runtime**
- Historical AWS evidence references infrastructure that no longer exists
- Risk classifications in the dataset have no documented derivation

## Security

- No credentials, API keys or internal endpoints in any published file
- Spreadsheet formula-injection protection on all CSV exports
- React auto-escaping (no raw HTML rendering)
- Strict shipment-ID validation (rejects SQL injection strings)
- Row-Level Security on all Supabase tables
- Quota functions executable only by the service role
- Fail-closed on any guardrail error

## License

MIT — see `LICENSE`.
