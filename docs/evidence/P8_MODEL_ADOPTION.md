# P-8 Architecture Review — Model Adoption Decision Record (2026-11-14)

Operator decision recorded as blueprint ledger **R6-2**
(`docs/blueprint/12_AUDIT_CORRECTIONS.md` §Revision 6). No new model
comparison or benchmark battery was run for this decision, per operator
instruction — the basis is the existing post-repair qualification evidence.

## Decision

| Item | Value |
|---|---|
| Provider | **NVIDIA NIM** (unchanged, R6-1; endpoint locked to `https://integrate.api.nvidia.com/v1`) |
| Production runtime model | **`z-ai/glm-5.3-flash`** (replaces `deepseek-ai/deepseek-v4.1-flash`) |
| Development coding assistant | GLM-5.3 via Ollama Cloud (builder-side, unaffected) |
| Basis | `NVIDIA_NIM_POST_REPAIR_QUALIFICATION.md` (+ `.json`, 2026-11-09): glm **14/15 complete_correct (93%)**, zero API failures, every guard rejection recovered, ~220k tokens (~40% below lightning); lightning failed the consistency bar (10/15, 3 fabrications incl. one confidently asserted, 1 recursion death, ~372k tokens) |
| Security posture | **Unchanged**: sql_guard untouched, endpoint lock, no-fallback, single provider, read-only views |

## Implemented with this decision

1. **F-A (data fix — fabrication root cause).** The dataset alert message
   "Observed 1.2 C outside configured 2 to 8 C" was ambiguous (readable as
   an excursion *magnitude*, producing candidate fabrications 0.8 °C /
   9.2 °C on both models — the same misreading the original fixture author
   made). All **16 occurrences** (incl. UC-003241 alert 7514, the battery
   shipment) reworded in BOTH stores to the unambiguous form:
   > Observed temperature 1.2 C, below configured range 2 to 8 C
   - `data/unified/alerts.csv` — 16 rows; the message now carries standard
     CSV quoting because it contains a comma (verified 8-field parse with
     the `csv` module; all other fields and CRLF line endings untouched).
   - `data/unified/unified_cold_chain.sqlite` — same 16 rows via
     transactional UPDATE (16/16, zero old rows remaining; all other
     columns verified unchanged; row counts unchanged).
   - `src/prompts/system_prompt.txt` — nudge added: retrieve out-of-band
     telemetry evidence around the alert time before interpreting alert
     text.
   - **Original audit evidence preserved verbatim**: `SHIPMENT_ACCURACY_
     AUDIT.md`, the qualification/final-validation/comparison reports and
     the historical harnesses still quote the original wording as the
     record of what was tested on 2026-11-08/09.
   - Out of scope (recorded, not done): other alert rows use the same
     "Observed X C outside configured A to B C" pattern with different
     values; only the exact 1.2 °C / 2–8 °C message named in F-A was
     reworded. A pattern-wide reword is a separate operator decision.
2. **F-B (code fix — success-loop recursion).** `SQLRejectionBudget`
   (`src/agent_tools.py`) now meters identical queries **regardless of
   guard outcome**: the exact same query executes at most once per agent
   request; an identical repeat returns a do-not-repeat instruction
   instead of executing again. Rationale (qualification finding F-B):
   lightning delay r2 looped 9× on *successful* duplicate queries (0 guard
   blocks) until the graph recursion limit — rejection metering alone
   could not stop it. Distinct valid queries remain unlimited; rejection
   metering (MAX_IDENTICAL=2 / MAX_DISTINCT=5) unchanged; rejected SQL is
   never rewritten; **`src/sql_guard.py` untouched**. The tool docstring
   now teaches the no-repeat rule.
3. **Model default → `z-ai/glm-5.3-flash`** in `src/orchestrator.py`,
   `scripts/check_nim.py`, `.env.example`, `.env.api.example`, the private
   `.env`, `README.md`, `docs/NVIDIA_NIM_RUNTIME.md`, `docs/INSTALLATION.md`,
   `integrations/librechat/README.md`, `GLM_5_3_PROJECT_GUIDELINES.md` §6,
   blueprint `00`/`02` banners, and the `test_orchestrator_config.py`
   default-model assertion.

## Verification

- **Offline test suite: 99 passed / 0 failed** (2026-11-14; was 92/92 —
  +7 new F-B regression tests in `tests/test_sql_tool_repair.py`).
  Baseline before changes: 92/92.
- **Key rotation (step 7): NOT ROTATED — live test withheld.** The current
  `NVIDIA_API_KEY` in `.env` was compared (SHA-256 of the value against
  SHA-256 of every `nvapi-*` string extracted from the local Claude Code
  chat transcripts; no key material displayed or logged) and **still
  matches a transcript-embedded key** — the exposure from 2026-11-03 is
  live. Per operator instruction the targeted live FastAPI/LangGraph
  integration test is **deliberately not run** until the key is rotated.
- **Observation (pre-existing, no action taken):** sqlglot's `tsql` reader
  parses `LIMIT`, so `validate_query` passes `LIMIT n` forms; a real SQL
  Server rejects them server-side (no security impact — still one
  read-only approved view, and the F-B meter now stops identical-repeat
  loops on that path). Pinned by regression test
  `test_limit_query_never_loops_and_is_never_rewritten`.

## Remaining blockers

1. **Rotate the NVIDIA key** (operator action; blocking all live requests):
   build.nvidia.com / NGC console → generate new key → replace
   `NVIDIA_API_KEY` in `.env` → revoke the old key. Do not paste the new
   key into chat.
2. Then: `python scripts/check_nim.py` + **one** targeted live tool-calling
   conversation through the running FastAPI gateway (closes the Phase 0
   C13 remnant; expect 1–4 min per answer at glm's qualification-measured
   latencies).
3. SQL tool execution against a real SQL Server remains a Phase 2 item
   (no local SQL Server / pyodbc); guard, F-A and F-B logic are fully
   covered by the offline suite.
4. Phases 1–7 not started; no AWS resources deployed.
