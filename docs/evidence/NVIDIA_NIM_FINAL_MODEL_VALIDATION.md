# NVIDIA NIM Final Model Validation — Final Validation Gate

Date: 2026-11-09 (battery executed 2026-11-08/09) · The decisive gate after the
model comparison: the two surviving candidates — `z-ai/glm-5.3-flash` and
`nvidia/nemotron-3.5-lightning-30b-a3b` — were tested against the PRODUCTION
SQL guard with dataset-derived results and database-anchored acceptance
checks. **Production configuration was not changed at any point.**
Evidence: [`NVIDIA_NIM_FINAL_MODEL_VALIDATION.json`](NVIDIA_NIM_FINAL_MODEL_VALIDATION.json)
· Harnesses: `architecture-review/nim_final_validation.py`,
`nim_validation_common.py` · Checker regression tests: `tests/test_validation_checks.py` (suite 65/65).

## 1. Validation environment (what changed vs. the earlier comparison)

- **The REAL `src/sql_guard.validate_query` runs before every execution.**
  Blocked SQL returns its actual error text into the LangGraph loop — never
  silently executed. This closed fidelity gap G-2.
- **Results are derived from the real dataset**: guard-passing T-SQL is
  transpiled (sqlglot) and executed against `data/unified/unified_cold_chain.sqlite`
  read-only, with per-view column allowlists and the production 10-row cap.
  Per-view columns outside the production FDE_VIEWS sets fail exactly as they
  would on SQL Server.
- Weather tool: REAL (Open-Meteo). SOP tool: labeled fixture (real SOP excerpt;
  Pinecone unavailable). Gateway/LibreChat not exercised.
- Battery: 2 candidates × 5 scenarios × 3 repetitions + 2 lightning
  remediation variants × 2 reps. ~110 live requests total (incl. ~7 lost to a
  harness save crash, disclosed below). 40 RPM never approached.
- Guard-fidelity discoveries baked in and unit-tested (19 new tests):
  the guard REQUIRES the `FDE_VIEWS.` schema prefix (unqualified `VW_SHIPMENTS`
  is rejected), requires TOP 1–100, and leniently accepts `LIMIT` past the
  parser — which then fails at SQL Server execution (mirrored).

## 2. Headline results

### `z-ai/glm-5.3-flash` — 15 conversations

| Scenario | Reps | Corrected verdicts |
|---|---|---|
| lookup | 3 | 2 PASS, 1 FAIL (rep1: never recovered from guard blocks, answered without the facts) |
| excursion | 3 | **0 PASS** — in all three, 8–9 guard blocks, zero recovery; answers could not cite the 1.2 °C / 23:45 excursion because the data was never retrieved |
| delay | 3 | 1 PASS (rep3), **2 hard ERRORS** — GraphRecursionError after 308 s / 526 s of blocked retries |
| sop | 3 | 2 PASS, 1 FAIL (rep2: see §4 fabrication) |
| weather | 3 | 3 PASS (2 after checker correction, §3) |
| **Total** | **15** | **8 PASS · 5 FAIL · 2 recursion-death errors** |

Guard interaction: **101 blocked queries** (91× "Only FDE_VIEWS approved unified
views may be queried", 9× TOP-required, 1× complex-form), 30 successful
queries, recovery from rejection in **5 of 15** conversations. Latency
154–480 s on completed runs; token usage 123,375 in / 36,169 out /
**19,725 reasoning**.

### `nvidia/nemotron-3.5-lightning-30b-a3b` — 15 conversations + 4 variant runs

**0 PASS, 19/19 conversations ended in `GraphRecursionError`.** Lightning
emitted unqualified view names on every query, received the guard's
"Only FDE_VIEWS approved unified views may be queried" error every time, and
**never once added the prefix** — it varied the query shape (with/without TOP,
even `SELECT *`) until the recursion limit killed the loop. **Zero successful
SQL queries in the entire battery.** Its earlier 9.3 s lookup "PASS" was an
artifact of the permissive fixture; under the real guard the model is
non-functional. Fast, and unusable.

## 3. Checker correction (disclosed for transparency)

The battery's stored JSON verdicts were computed with a checker that
whitelisted the SOP's 0.0–4.0 °C thresholds only in the SOP scenario. Since
the SOP tool is callable in every scenario, three conversations
(delay rep3, weather rep2, weather rep3) were falsely failed for "fabricated
temperatures" that were legitimate SOP citations. The checker is fixed
(SOP thresholds allowed globally; regression test added; suite 65/65), and
this report's per-conversation verdicts are the corrected classification —
recomputed from the stored per-violation value+context evidence. The raw
pre-correction verdicts remain in the JSON as recorded.

## 4. Genuine fabrication and honesty findings (glm)

- **sop rep2 — genuine fabrication of critical measurements:** unable to
  retrieve the excursion data, glm wrote "*whether the deviation was high
  (9.2 °C) or low (0.8 °C)*" — inventing plausible excursion values instead
  of stating it did not know. Both values are absent from the dataset
  (the real excursion is 1.2 °C, LOW side). This is the exact failure mode
  the acceptance checks exist to catch.
- **delay rep3 (PASS after correction):** recovered, cited honest evidence
  limits, and derived a slowdown narrative from real telemetry — the honest
  pattern the checks reward.
- **lookup rep1 (FAIL):** answered without the cargo type, band, or verdict —
  it never recovered from guard blocks and produced an evidence-free answer
  rather than declining to answer.

## 5. Lightning accuracy experiment (§4 of the commission)

The two remediation variants — narrow-column + explicit column/unit
instructions, and `thinking=false` — were both run on the excursion scenario,
2 reps each. **Both failed identically to the baseline: recursion death after
~10 unqualified queries.** The humidity-vs-temperature error never came into
play because lightning cannot reach the data at all under the production
guard. No instruction variant fixes non-adaptability to the guard; remediation
of lightning is moot until that is solved.

## 6. Assessment against the six recommendation requirements

| Requirement | glm-5.3-flash | lightning-30b |
|---|---|---|
| Uses the real SQL guard correctly | ❌ recovers in only 5/15 conversations; 91 prefix blocks; zero recovery on the excursion scenario | ❌ zero successful queries in 19 runs |
| Produces no fabricated critical measurements | ❌ invented 9.2/0.8 °C excursion candidates in sop rep2 | n/a (never held data) |
| Correctly identifies known temperature excursions | ❌ 0/3 — could not retrieve the evidence in any rep | ❌ (no data ever retrieved) |
| Handles empty and incomplete datasets honestly | ⚠ mixed — honest in recovered delay/weather runs; speculation instead of "unknown" in sop rep2 | n/a |
| Supports LangGraph tool calling reliably | ❌ 2/15 conversations died at the recursion limit | ❌ 19/19 died at the recursion limit |
| Maintains acceptable response times | ⚠ 154–480 s on completed runs (targets were <30 s lookup / <45 s investigation) | n/a |

## 7. Verdict

**No model has passed the final validation gate.**

Per the commission's explicit instruction, no primary model is recommended at
this time. `z-ai/glm-5.3-flash` remains the closer candidate (8/15 corrected
passes; capable of honest, accurate answers **when it retrieves data**) but
fails the guard-compliance, excursion-identification, and reliability bars.
`nvidia/nemotron-3.5-lightning-30b-a3b` is **disqualified**: it cannot execute
a single guarded query.

## 8. Root cause and the recommended path (for review — nothing changed)

The dominant failure for BOTH candidates is **guard adaptation to the
`FDE_VIEWS.` schema-qualification requirement** — 91 of glm's 101 blocks and
100% of lightning's blocks are that single error. The guard's behavior is
production-faithful (SQL Server would reject unqualified names identically),
but the models are given no worked example of a valid query: the tool
docstring lists view names without showing the required
`FROM FDE_VIEWS.VW_…` form, and glm self-corrects only from the error text
(and lightning not at all).

Recommended sequence for architecture review:
1. **Amend the `query_telemetry_db` tool docstring** (production change, out
   of scope here) with one explicit example:
   `SELECT TOP 10 recorded_at, temperature_c FROM FDE_VIEWS.VW_TELEMETRY WHERE shipment_id = 'UC-003241'`.
2. **Re-run this gate unchanged.** If glm then clears the excursion scenario
   and stops hitting recursion deaths, it is the primary model on the
   corrected evidence. If it still fails — no NIM-hosted model has passed,
   and the operator decision is: relax latency/interaction targets around
   glm's honest-but-slow profile, or continue the search (re-probe the
   timed-out `openai/gpt-oss-20b`, survey further servable models).
3. Regardless of model choice: keep the acceptance-check harness — it caught
   one fixture defect, one checker defect, one genuine fabrication, and one
   total non-adaptability that the earlier permissive benchmarks all missed.

## 9. Remaining limitations

- SOP retrieval is simulated (real SOP text, no Pinecone latency/relevance
  behavior); weather is live; SQL Server's own network latency is not
  represented (SQLite execution is ~0 ms).
- The 2 harness-side incidents are disclosed: the first battery launch lost
  ~7 glm lookup requests to a JSON-set serialization crash (fixed: per-rep
  incremental saves + save smoke-test); the checker's SOP-threshold
  false-positive was corrected post-run (§3) — both are recorded so no
  measurement silently changed.
- n=3 per scenario bounds statistical strength; glm's latency variance
  (154–480 s) is large.
- Token usage for lightning's failed runs is unrecorded (recursion-death path
  did not persist turn metadata) — its 19 failures cost little (~30 s each).

**Stopped for review and approval. Production configuration is untouched.**
