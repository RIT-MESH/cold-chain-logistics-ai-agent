# Cold-Chain Logistics AI Agent

A production-grade, evidence-grounded AI agent for cold-chain logistics
operations — answering shipment, telemetry, incident, and SOP questions over
a **guarded, read-only** SQL Server dataset, behind an OpenAI-compatible
gateway that plugs into LibreChat.

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
invent a procedure citation, and stay useful when the LLM provider is having a
bad day.

## Key features

- **Conversational shipment intelligence** — reports, contextual follow-ups
  ("any problem in *this* shipment?"), temperature/humidity history, incident
  investigation with notification state, and two-shipment comparisons with
  explicitly stated criteria.
- **Hardened SQL access** — one allowlisted-tool design: schema-qualified
  `FDE_VIEWS` only, `TOP`-capped explicit-column SELECTs, no joins/CTEs/UNION,
  read-only database identities, full TLS verification. Rejected SQL is never
  rewritten, metered so loops terminate. See `src/guards/sql_guard.py`.
- **Deterministic evidence prefetch** — recognized shipment questions
  pre-run fixed guard-valid queries and answer in ONE synthesis turn
  (seconds) instead of multi-turn tool loops.
- **Intent-based answer validation** — strict ground-truth checks for the
  incident+SOP flow, light checks for history/comparison, honest-unavailability
  rules, one bounded corrective retry, and raw tool-call syntax never reaches
  a user.
- **Deterministic fallback** — if synthesis fails, a clearly labeled
  evidence-only summary is assembled from the same guarded SQL. No blank
  responses, ever.
- **SOP retrieval with citations** — Pinecone vector search over procedure
  documents with complete bracketed citations (title/version/section/status),
  access-filtered namespaces, and an honest failure story.
- **Audited by design** — every request, tool call, guard decision, answer
  source, and failure class emits a structured JSON line correlated by
  request id (CloudWatch in the AWS deployment).
- **Event pipeline** — S3→SQS→worker→RDS synthetic telemetry ingestion with
  DLQs, idempotency, linkage validation, exponential-backoff notification
  reconciliation, and CloudWatch alarms.
- **Observability** — six Grafana dashboards (operations, per-shipment
  telemetry, logistics/custody, live AWS infrastructure, incidents, agent
  audit) generated from code, with honest empty-states and a daily
  Cost-Explorer billing snapshot.

## Architecture

```
                    ┌────────────────────────────────────────────────┐
 LibreChat ────────▶│ FastAPI OpenAI-compatible gateway (api/main.py) │
 (browser)          │  context resolver → intent → validation        │
                    └───────┬───────────────────────────┬────────────┘
                            │                           │
                    ┌───────▼────────┐          ┌───────▼─────────┐
                    │ LangGraph ReAct│          │ deterministic    │
                    │  (glm reasoner)│          │ evidence prefetch│
                    └───────┬────────┘          └───────┬─────────┘
                            │ tools                     │ fixed SQL
                    ┌───────▼───────────────────────────▼─────────┐
                    │ query_telemetry_db (SQL GUARD, read-only     │
                    │ FDE_VIEWS identities, TLS)                   │
                    │ search_compliance_sop (Pinecone, citations)  │
                    │ fetch_corridor_conditions (weather)          │
                    └──────────────────────────────────────────────┘
                            │                        │
                    ┌───────▼───────┐      ┌────────▼────────┐
                    │ SQL Server    │      │ NVIDIA NIM       │
                    │ (RDS)         │      │ glm-5.3-flash +  │
                    │ + FDE_VIEWS  │      │ nemotron synth.  │
                    └───────────────┘      └─────────────────┘
```

- **AI runtime:** NVIDIA NIM (OpenAI-compatible endpoint) —
  `z-ai/glm-5.3-flash` for the ReAct/tool loop,
  `nvidia/nemotron-3.5-lightning-30b-a3b` for the single-turn evidence
  synthesis, bounded per-path timeouts.
- **Orchestration:** LangGraph state graph; per-request unique thread ids
  (no cross-user state), recursion-capped.
- **Context:** deterministic referent resolution from the request's own
  message history — "this shipment"/"it"/"them" resolve without any global
  state (isolation by construction).
- **Security model:** see `docs/blueprint/05_SECURITY_MODEL.md`.

## Repository layout

| Path | Contents |
|---|---|
| `src/agent/` | orchestrator, context resolver, prefetch, evidence fallback, validators, prompts |
| `src/guards/` | the SQL guard |
| `src/gateway/` | FastAPI gateway, Streamlit dashboard |
| `src/telemetry/` | Phase-4 event worker + reconciliation |
| `src/retrieval/` | SOP embedding pipeline |
| `architecture-review/` | NIM benchmarking/validation harnesses |
| `infra/` | Terraform (network, compute, RDS, secrets, OIDC, web, events modules) |
| `docker/`, `integrations/` | compose stack, Grafana dashboards/provisioning, LibreChat config |
| `examples/synthetic-data/` | the complete synthetic dataset (CSV + SQLite + schemas) |
| `docs/` | blueprint design set, curated evidence reports, engineering guides |
| `tests/` | the offline regression suite |

## Running it

The application was developed against a real AWS deployment (Terraform in
`infra/`, docker compose in `docker/`), and also runs fully **offline**
against the bundled SQLite dataset for demonstration:

```bash
pip install -r requirements.txt
export LLM_PROVIDER=NVIDIA_NIM
export NVIDIA_API_KEY=YOUR_NVIDIA_API_KEY     # never commit real values
cp examples/env.example .env                  # then edit
streamlit run src/gateway/ui.py                # offline dashboard mode
uvicorn src.gateway.api.main:app --port 8000   # OpenAI-compatible gateway
```

All configuration is documented in `examples/env.example` — placeholder
values only. See `docs/guides/LIBRECHAT_REUSABLE_IMPLEMENTATION_GUIDE.md`
for the engineering lessons behind the gateway design.

## Test coverage

An offline suite of **382 tests** covers: the SQL guard, validator dispatch
(intent vs. mention), context resolution (referents, switching, isolation),
comparison handling, geography and risk-honesty rules, evidence fallback,
SOP-unavailability, dashboard provisioning guards, telemetry processing, and
infrastructure guardrails.

```
382 passed in ~7 s
```

## Verified performance (measured, not estimated)

- Model qualification: glm-5.3-flash 14/15 complete-correct answers with
  zero API failures in the post-repair battery (per
  `docs/evidence/NVIDIA_NIM_POST_REPAIR_QUALIFICATION.md`); candidate models
  that fabricated answers were rejected in evidence-gated validation.
- Multi-turn guarded E2E over live RDS: correct shipment record, the single
  true excursion corroborated from telemetry, evidence gaps disclosed,
  non-causal correlations labeled (`docs/evidence/PHASE5_AGENT_AUDIT.md`).
- Under a full LLM-provider outage (measured 284–302 s time-to-first-token),
  the acceptance conversation still completed 8/8 — 5 questions via validated
  synthesis, 3 via the labeled deterministic fallback; shipment questions
  answered in **7–38 s** while general chat was degraded.
- Per-answer latency in normal serving: ~10 s lookup to ~2–4 min for
  multi-tool investigations (NIM serving variance is documented in
  `docs/evidence/NVIDIA_NIM_LATENCY_BENCHMARK.md`).

## Known limitations

- General (non-shipment) questions depend on the ReAct path and inherit
  provider latency; the fallback covers shipment-scoped questions only.
- SOP citation quality depends on embedding-service availability; failure is
  stated honestly rather than papered over.
- The synthetic dataset intentionally mixes regions and contains modeled GPS;
  the agent reports coordinates without place labels by design.
- Risk classifications in the data have no documented derivation; the agent
  never invents one.

## Deployment note (honesty)

This repository demonstrates the **application and its engineering process**.
A live AWS deployment existed during development (single-tenant,
authentication-gated — not a public service); its operational runbooks,
session records, approval packages and credential material are deliberately
NOT part of this repository. The permanent public demonstration is the
offline portfolio: **https://huggingface.co/spaces/ritmesh/cold-chain-logistics-ai**
which includes an interactive AI Chat assistant over the same synthetic
dataset.

## License

MIT — see `LICENSE`.
