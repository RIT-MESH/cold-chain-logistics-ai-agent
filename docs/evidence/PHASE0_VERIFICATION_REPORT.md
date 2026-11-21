# Phase 0 Verification Report — Cold-Chain Logistics AI-Assistant

Date: 2026-11-03 · Scope: **Phase 0 only** (per approval of the Revision 5
blueprint baseline). No AWS provisioning, no `terraform apply`, no container
deployment, no Phases 1–6 work was started. No API keys were exposed or
committed. No LLM provider substitution.

## 1. Repository review against the approved Revision 5 blueprint

The repository matches the blueprint's "current implementation" columns
exactly; the blueprint's target-state items (Terraform, RDS deployment,
executed grants, ALB/Cognito, event pipeline on AWS, audit log) remain
unimplemented pending Phases 1–6, as documented:

| Blueprint area | Repo status |
|---|---|
| Streamlit UI (SQLite mode) | implemented; dataset verified (§5) |
| FastAPI gateway (`api/main.py`) | implemented; contract verified (§4) |
| LangGraph orchestrator + 3 tools | implemented; offline tool-loop verified with mocks (§6) |
| SQL guard (`src/sql_guard.py`) | implemented; threat cases extended (§7) |
| Ollama Cloud GLM-5.3 config | verified in code (§3); live run PENDING (§8) |
| Unified dataset `data/unified/` | verified against manifest (§5) |
| Floci/telemetry worker | implemented, offline unit tests pass; live emulator run out of Phase 0 scope |
| Terraform / AWS infra | correctly absent (Phase 1+) |

One pre-existing documentation finding (recorded, not fixed — content would
be invented): `GLM_5_3_PROJECT_GUIDELINES.md` §2 references
`docs/LEGACY_INSTRUCTIONS_UNSAFE.md`, which does not exist in the repository
(gap G-HY-3, blueprint `11`).

## 2. Defects found and corrections made

| ID | Defect | Correction | Files changed |
|---|---|---|---|
| P0-DEF-1 (G-HY-1) | `tests/test_llm_provider.py` read `src/orchestrator.py` without `encoding="utf-8"` — crashed under the Windows cp1252 codec (1/21 baseline failure; passed on Linux CI) | explicit `encoding="utf-8"` on all `read_text()` calls in that test file | `tests/test_llm_provider.py` |
| P0-DEF-2 | **Security-ordering defect**: `src/agent_tools.py` constructed the SQLAlchemy engine (and loaded the DB driver) **before** running `validate_query`, so a disallowed query still triggered DB-resource creation and, with the driver missing, crashed the tool before the `SECURITY BLOCK` result could be returned | `validate_query` now runs **first**; engine construction moved inside the guarded try/except. No behavior change for valid queries | `src/agent_tools.py` |
| P0-DEF-3 (G-HY-2) | `.env.streamlit-embed.example` pointed at the retired `fleet-history` Grafana dashboard | corrected to the unified dashboard URL | `.env.streamlit-embed.example` |

P0-DEF-2 is the only application-source change, it is within Phase 0 scope
("fix genuine defects"), and it *enforces* the approved architecture (guard
before any database interaction — blueprint `05_SECURITY_MODEL.md` §1,
guidelines §5.3) rather than modifying it.

## 3. Ollama Cloud GLM-5.3 configuration verification

- `LLM_PROVIDER=OLLAMA_CLOUD` is the default; missing `OLLAMA_API_KEY`
  → `RuntimeError` (verified behaviorally, §8/§9 tests).
- `OLLAMA_CLOUD_MODEL` defaults to `glm-5.3` (verified behaviorally).
- `OLLAMA_CLOUD_BASE_URL` is locked to exactly `https://ollama.com/v1`;
  any other value raises `ValueError` (verified behaviorally).
- No automatic fallback: unsupported providers, empty model, and missing
  key all hard-fail (verified behaviorally).
- **No `.env` file exists in this environment; no OLLAMA_API_KEY is
  available.** `python scripts/check_ollama_cloud.py` exits with
  "Set OLLAMA_API_KEY in your private .env or environment first"
  (exit code 1) — correct behavior.

## 4. FastAPI gateway and LibreChat-compatible contract (live uvicorn smoke)

Run against a real `uvicorn api.main:app` process (127.0.0.1:8765):

| Check | Result |
|---|---|
| `GET /health` | **200** `{"status":"ok","service":"cold-chain-gateway"}` |
| `GET /v1/models` without auth | **401** |
| `GET /v1/models` wrong bearer | **401** |
| `GET /v1/models` correct bearer | **200**, lists exactly `cold-chain-agent` |
| `POST /v1/chat/completions` wrong model | **400** `Unknown model: wrong-model` |
| `POST /v1/chat/completions` without Ollama creds | **502** "Agent execution failed; inspect gateway logs" — server log shows `RuntimeError: OLLAMA_API_KEY is required when LLM_PROVIDER=OLLAMA_CLOUD` |

The 502 path is the required safe-failure: **no silent local-model
fallback** at the API boundary. The OpenAI-compatible shape (model list,
completion envelope, SSE `[DONE]` contract) is additionally covered by
`tests/test_gateway.py` (offline TestClient).

## 5. Unified dataset verification (`tests/test_dataset.py`, all passing)

- File presence: SQLite DB + all 7 CSVs ✅
- Table counts equal `DATASET_MANIFEST.json` exactly: shipments 4,020 ·
  vehicles 196 · sensors 4,028 · telemetry 64,120 · custody 10,030 ·
  alerts 8,094 · route_events 64,120 ✅
- `PRAGMA foreign_key_check`: **zero violations** ✅
- Date coverage: min/max of `departed_at`, `recorded_at`, `alert_at`,
  `handed_at` all within manifest bounds 2021-01-01 → 2026-10-31 ✅
- Yearly shipment counts equal the manifest per-year values ✅
- Relational invariants: unique shipment PK; zero telemetry orphans ✅
- Synthetic labels: zero rows with NULL `synthetic_test_data` in
  shipments/telemetry ✅

## 6. LangGraph tool calling (offline, mocked LLM)

`tests/test_orchestrator_toolflow.py` drives the **real** compiled graph
(`fde_agent`) with scripted LLM responses, so the reasoner→ToolNode→reasoner
plumbing is exercised end to end without credentials:

- Weather tool: one tool call dispatched, real tool executed against a
  stubbed HTTP response, "CURRENT WEATHER … NOT LIVE FLEET TELEMETRY"
  labeling present, final assistant answer returned ✅
- SQL tool: a `DELETE` query is rejected by the guard **inside the real
  tool** → `SECURITY BLOCK: …` returned to the LLM; no DB connection
  attempted ✅ (this is the test that exposed P0-DEF-2)
- SOP tool: without `PINECONE_API_KEY` returns
  "PINECONE_API_KEY is not configured; SOP search is unavailable" — an
  explanatory failure, never fabricated compliance text ✅

## 7. SQL guard (offline fixtures)

`tests/test_sql_guard.py` now covers 6 allow cases (including
`COUNT(*) … WHERE` without TOP) and 16 deny cases: mutations, base tables,
`SELECT *`, stacked statements, joins, retired `VW_FLEET_HISTORY`, **plus
new: `EXEC` (procedure execution), `TOP 0`/`TOP 101` bounds, CTEs, approved
view name in wrong schema (`dbo.VW_TELEMETRY`), `UNION`.** All pass.

## 8. Test results (final run)

- **43 passed · 0 failed · 0 skipped** (baseline before Phase 0:
  20 passed / 1 failed)
- New/changed test files: `tests/test_dataset.py` (7 tests),
  `tests/test_orchestrator_config.py` (5 tests),
  `tests/test_orchestrator_toolflow.py` (3 tests),
  `tests/test_sql_guard.py` (+11 cases), `tests/test_llm_provider.py`
  (encoding fix)
- Environment: Windows 11, Python 3.12; same suite passes the Linux CI
  workflow unchanged (the fixed test was Windows-only broken before).

## 9. Live validation status: PENDING (not faked)

**No OLLAMA_API_KEY is present in this environment**, therefore — per the
approval's explicit instruction — live Ollama Cloud connectivity and live
GLM-5.3 tool calling were **NOT executed and are NOT claimed**. What
remains pending for the first credential holder:

1. `python scripts/check_ollama_cloud.py` → expect "Cloud model response: OK".
2. One small live gateway conversation that must invoke
   `fetch_corridor_conditions` and cite its output (proves real
   `tool_calls` round trip; blueprint Phase 0 gate).
3. Confirm whether Ollama's API returns usage fields (drives decision U-6).

## 10. Resource usage and API usage

| Resource | Usage during Phase 0 |
|---|---|
| Ollama Cloud API requests | **0** (no credentials) — subscription spend $0.00 |
| Open-Meteo / Pinecone / SQL Server calls | 0 (all stubbed/mocked or local) |
| External network calls | none beyond pip package installation (langgraph, langchain-openai, langchain-pinecone, openai + deps) |
| Test suite runtime | ≈2.3 s (offline) |
| Errors encountered | the baseline Windows codec failure (P0-DEF-1, fixed); the P0-DEF-2 ordering defect surfaced by the new SQL-tool test (fixed); one test-authoring bug in `test_orchestrator_toolflow.py` (fixed before final run) |

## 11. Outstanding limitations and prerequisites for Phase 1

1. **Ollama credentials** — Phase 0's live gate (§9) must be executed by a
   credential holder before Phase 1 begins; results go in
   `docs/evidence/`.
2. **AWS account, region, budget** — unresolved decisions U-2/U-3 (region,
   RDS tier) must be answered and a current AWS Pricing Calculator export
   produced before any provisioning (blueprint Phase 1 hard precondition).
3. **Domain (U-4)** — needed by Phase 3 only, but should be requested early.
4. notes.pdf remains unread (scanned, no OCR in this environment) — U-1.
5. `docs/LEGACY_INSTRUCTIONS_UNSAFE.md` referenced by the guidelines does
   not exist (G-HY-3) — harmless, recorded; no content was invented.
6. Windows-local caveat: `pyodbc` is not installed on this machine, so the
   SQL tool's live-connection path was exercised only through the guard and
   the safe-failure envelope; MSSQL-mode Streamlit remains unverified
   without SQL Server (as in the blueprint).

## 12. Change inventory (all within Phase 0 scope)

| File | Change |
|---|---|
| `src/agent_tools.py` | guard-before-engine ordering fix (P0-DEF-2) |
| `tests/test_llm_provider.py` | encoding fix (P0-DEF-1) |
| `tests/test_sql_guard.py` | +11 threat cases |
| `tests/test_dataset.py` | new — 7 dataset verification tests |
| `tests/test_orchestrator_config.py` | new — 5 no-fallback/config behavior tests |
| `tests/test_orchestrator_toolflow.py` | new — 3 offline tool-loop tests |
| `.env.streamlit-embed.example` | stale dashboard URL fix (P0-DEF-3) |
| `docs/evidence/PHASE0_VERIFICATION_REPORT.md` | this report |

**Phase 0 is complete. Stopping here. Phases 1–6 are NOT started and await
separate approval.**