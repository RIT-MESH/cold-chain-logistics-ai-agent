# Phase 0 C13 — Live Gateway Tool-Calling Verification (2026-11-15)

The final remaining Phase 0 item: one real FastAPI → LangGraph → NVIDIA NIM
conversation through this project's own gateway
(`api/main.py`), per blueprint 09 Phase 0 and operator instruction of
2026-11-15. No model benchmark or comparison was run (operator instruction);
no AWS infrastructure was touched.

## Pre-conditions

| Item | Result |
|---|---|
| Runtime model config | `.env` `NVIDIA_MODEL=z-ai/glm-5.3-flash`, `NVIDIA_BASE_URL` locked; `orchestrator.py` hard-fails any other provider (R6-1) |
| Key status | ⚠️ Operator directed to proceed with the existing key "for now". Hash-compare (SHA-256, key never displayed) re-confirmed the `.env` key **still matches the 2026-11-03 transcript exposure** (5 occurrences in `2e36afab…jsonl`, incl. the original "take this new key" paste). The probe succeeded, so the key also appears **not actually revoked**. Rotation remains outstanding — operator-accepted risk for this test only. |
| Gateway auth | `FDE_API_KEY` generated programmatically into `.env` (never displayed; sha256 prefix `3d01fd4b`) |
| Offline suite | `pytest tests/` — **99/99 PASS** (guard, budget, tool-repair, config, gateway-contract tests) |

## Executed verification and results

1. **NIM connectivity** — `python scripts/check_nim.py`: **PASS** — response
   `OK`, 17 prompt / 54 completion tokens.
2. **Gateway auth enforcement** (no LLM cost): `GET /v1/models` and
   `POST /v1/chat/completions` without key → **401**; wrong bearer → **401**;
   correct bearer → **200**; `/health` → 200. Caller-controlled system/developer/
   tool messages are ignored by `convert_history` (verified by contract tests).
3. **One live tool-calling conversation** (`POST /v1/chat/completions`,
   non-stream, user asks for weather at 51.95/4.14 plus latest telemetry for
   shipment UC-003241):
   - **HTTP 200, elapsed 95.0 s** — within the glm-5.3-flash multi-tool median
     band (78–205 s, post-repair qualification).
   - **Token accounting (C12):** 6,696 prompt / 1,567 completion / 8,263 total;
     returned in the response `usage` field from the same dict the
     `token_usage` log line emits.
   - **LangGraph loop:** multiple tool turns, terminated normally with a final
     assistant answer (no `GraphRecursionError`, no budget intervention).
   - **Weather tool invoked 3×** (`fetch_corridor_conditions` 51.95/4.14) —
     every attempt hit the external open-meteo API timeout; the model reported
     the failure honestly, explicitly refused to substitute general knowledge
     as measurement, and recommended a retry.
   - **SQL tool exercised 3×** with three *distinct* guard-valid queries
     (`FDE_VIEWS.VW_TELEMETRY` TOP 100 ordered by `recorded_at`, reduced-column
     TOP 50 variant, `FDE_VIEWS.VW_SHIPMENTS` TOP 10 header) — **zero SECURITY
     BLOCKs** (guard ran first on every call, `agent_tools.py:227`); execution
     returned the documented local constraint `"Database query failed; inspect
     service logs."` (no local SQL Server, pyodbc not installed — live SQL
     execution is a Phase 2 item). The model correctly diagnosed a
     service-level failure, did not fabricate readings, and cross-referenced
     the synthetic-data disclosure unprompted.
4. **Leak scan on the full HTTP response:** no `nvapi-` prefix, no gateway
   key, no full NVIDIA key, no `reasoning` content — the gateway returns only
   the final `AIMessage.content` (`api/main.py:103-108`).

## Findings from this run (new)

- **C13-F1 (environment defect, workaround applied):** the emoji in
  `orchestrator.py:91` (`print("⚙️ Compiling…")`) raises
  `UnicodeEncodeError` at module import under Windows cp1252 console encoding,
  which killed the first live request with HTTP 502 in 2.6 s. Workaround:
  launch with `PYTHONUTF8=1` (environment-level, zero code change). The AWS
  deployment target (Linux, UTF-8 default) is unaffected. *Optional cosmetic
  code fix (drop the emoji or wrap the print) awaits operator decision.*
- **C13-F2 (external network block):** `api.open-meteo.com` is unreachable
  from this machine — TLS handshake times out even outside the tool's 6 s
  timeout and outside any sandbox, while `integrate.api.nvidia.com` works from
  the same process. Consequence: the blueprint-09 C13 clause "invoke
  `fetch_corridor_conditions` **and cite its output**" is only half-satisfiable
  here — the tool was invoked (3×) and its failure path was exercised, but a
  successful weather citation cannot be produced from this network. The tool
  call path itself is live-verified (invocation, tool-result round-trip into
  the loop, honest reporting).
- **C13-F3 (minor, logging visibility):** the `token_usage` line is emitted at
  INFO on the `cold-chain.gateway` logger, which default uvicorn logging does
  not display locally. The counter itself is verified (it produced the
  response `usage` field). Wire logger configuration for CloudWatch at
  Phase 3 (ColdChain/NimTokens).

## C13 status — recommendation for operator ratification

All five acceptance steps of the 2026-11-15 operator instruction **PASS**
(connectivity; one live gateway conversation; glm-5.3-flash active; guard
enforced with correct, non-fabricated responses; no reasoning/credential
leakage). Against the blueprint-09 wording, the `fetch_corridor_conditions`
**invocation** requirement is met; **citing its output** is impossible from
this network (C13-F2).

**Recommendation: close Phase 0 C13 on this evidence**, recording C13-F2 as
the reason the weather citation is represented by its validated failure path,
and re-running a weather-citing conversation opportunistically once open-meteo
is reachable (or from the deployment environment in Phase 2/3). The
outstanding NVIDIA key rotation (operator-accepted risk above) should be
scheduled before or with the Phase 2 deployment secrets work.

*No blueprint or ledger rows were amended by this document — closure and any
R6-x ledger entry await operator decision, per project convention.*
