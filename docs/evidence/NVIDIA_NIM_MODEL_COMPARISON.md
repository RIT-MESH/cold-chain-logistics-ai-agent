# NVIDIA NIM Model Comparison — Fast Model Selection and Accuracy Verification

Date: 2026-11-08 · Commissioned after the latency-benchmark review: keep NVIDIA
NIM as the exclusive runtime provider, reopen the model-selection decision.
**Production runtime config was NOT changed.** Companion evidence:
[`NVIDIA_NIM_MODEL_COMPARISON.json`](NVIDIA_NIM_MODEL_COMPARISON.json) (all
measurements), [`SHIPMENT_ACCURACY_AUDIT.md`](SHIPMENT_ACCURACY_AUDIT.md)
(fixture correction + acceptance-check rationale). Harnesses (rerunnable):
`architecture-review/nim_servability_probe.py`, `nim_model_comparison.py`.

## 1. Method

- Five configurations, identical prompts, ground-truth-corrected fixtures
  (the excursion is hidden from unfiltered TOP-10 results; finding it
  requires a filtered query — as in production SQL).
- Per config: streamed greeting (first-token latency), shipment-lookup ReAct
  loop, excursion-investigation ReAct loop (the LangGraph tool round trip).
- **Programmatic acceptance checks** on every answer against database ground
  truth (exactly one excursion, 1.2 °C LOW side at 23:45:09, zero readings
  above 8.0 °C; see the accuracy audit) — a fluent answer that contradicts
  the database fails visibly.
- ~35 sequential live requests (org limit 40 RPM never approached).

## 2. Servability (Task 1 — authenticated probes, not the catalog)

| Model | Probe result | Class |
|---|---|---|
| `nvidia/nemotron-3.5-bolt-e4b` | OK · **0.5 s** · 0 reasoning tokens | servable |
| `nvidia/nemotron-3.5-lightning-30b-a3b` | OK · 1.6 s · 122 reasoning | servable |
| `z-ai/glm-5.3-flash` | OK · 33.3 s · 113 reasoning | servable |
| `openai/gpt-oss-20b` | **timeout at 120 s** (APITimeoutError) | not servable (this session) |

Confirmed dead earlier via 404 (latency benchmark): `ai21labs/jamba-1.5-large-instruct`,
`aisingapore/sea-lion-7b-instruct`, `meta/llama-3.2-90b-vision-instruct`,
`microsoft/phi-3-vision-128k-instruct`, `microsoft/phi-3.5-moe-instruct`.
The public `/v1/models` list (84 entries) demonstrably contains non-servable
models; servability requires authenticated probes.

## 3. Results (Task 2 — live measurements)

### 3a. Speed

| Config | Greeting total (TTFT) | Lookup loop | Excursion loop | Targets met? |
|---|---|---|---|---|
| deepseek v4.1-flash **default** | 172.2 s (107.4 s) | 152.2 s | **451 s → HTTP 500, no answer** | none |
| deepseek v4.1-flash **thinking-off** | 65.8 s (**8.5 s**) | 155.7 s | 248.9 s | none |
| **z-ai/glm-5.3-flash** | 57.5 s (41.7 s) | **76.2 s** | **150.1 s** | none (2–6× better than default) |
| **nemotron lightning-30b-a3b** | 15.2 s (10.4 s) | **9.3 s** | 61.2 s | chat ≈15 s borderline; **lookup <30 s ✓** |
| **nemotron bolt-e4b** | **2.6 s (0.67 s)** | **3.3 s ✓** | **5.8 s ✓** | all three latency targets ✓ |

### 3b. Accuracy — programmatic acceptance checks (Task 3 enforcement)

| Config | Lookup | Excursion investigation | Detail |
|---|---|---|---|
| deepseek default | **PASS** | **n/a — endpoint HTTP 500** | issued the correct filtered low-side query after 7 total tool calls, but the server errored before the final answer (second reliability failure in two sessions) |
| deepseek thinking-off | **PASS** | **FAIL** | saw only the unfiltered TOP-10 sample (no excursion visible), never issued a filtered query, concluded "**No temperature excursions**" — fluent, wrong, and caught by the check |
| **z-ai/glm-5.3-flash** | **PASS** | **PASS — the only one** | queried shipments/telemetry/alerts/custody; when the telemetry sample didn't cover the excursion window, it correctly used the ALERT record ("Observed 1.2 C outside configured 2 to 8 C") and explicitly disclosed the sample gap. Cited 1.2 °C, 23:45, low-side, no fabricated highs |
| nemotron lightning-30b | **PASS** | **FAIL** | misread columns in its own wide SELECT — reported **humidity 81.0 as a temperature**, built a table of phantom high-side breaches. Fast but sloppy |
| nemotron bolt-e4b | **PASS** | **FAIL** | shallow: two unfiltered queries, never filtered for out-of-band readings or checked alerts, concluded "no excursions". Fastest, and wrong |

### 3c. Reasoning tokens & leak behavior

| Config | Greeting reasoning tok | Loop reasoning tok | reasoning_content in stream |
|---|---|---|---|
| deepseek default | 114 | 554 (excursion) | **leaks** |
| deepseek thinking-off | 0 | 0 | no leak |
| glm-5.3-flash | 748 | 66 (lookup) / **1,560 (excursion)** | **leaks** |
| lightning-30b | 1,019 | 760 / 4,673 | **leaks** |
| bolt-e4b | 0 | 0 | no leak |

Reasoning-disable probes: `chat_template_kwargs.thinking=false` is accepted
by **lightning (0.43 s, 0 reasoning)** and by glm-flash (21.9 s, reasoning
reduced to 57 but not eliminated — different mechanism than DeepSeek's).

### 3d. Production SQL-guard compatibility (checked against the real `src/sql_guard.py`)

The guard parses T-SQL, **requires TOP 1–100 on non-aggregate queries**, and
rejects `LIMIT` as invalid syntax. **No model produced a fully guard-clean
query set in this run** — the benchmark fixture did not enforce the guard's
TOP rule (fidelity gap, disclosed):

- `TOP` used correctly by lightning (lookup `TOP 1`) and glm (`TOP 100`) —
  but both also issued no-TOP queries that the production guard would reject.
- **bolt emitted `LIMIT 1`** — correction from the final-validation executor
  tests: sqlglot's lenient T-SQL parser maps `LIMIT` onto TOP's node, so the
  **guard ACCEPTS it**; the failure point is the SQL Server execution
  (T-SQL has no `LIMIT`) — "Database query failed". Its speed results are
  real, but its tool compatibility is currently the weakest.
- DeepSeek's queries (both configs) were no-TOP and would draw
  "Use TOP to cap SQL results" errors in production.

In production each rejection returns a clear SECURITY BLOCK message the agent
can retry from — at the cost of extra turns/latency, and model adaptability
to guard errors is **untested** (follow-up gate G-2).

## 4. Ranking (Task 4) — speed, reliability, tool compatibility, accuracy, usage

| Rank | Candidate | Verdict |
|---|---|---|
| **1** | **z-ai/glm-5.3-flash** | The **only model that answered everything correctly** — including the hard excursion investigation, where it did honest evidence-gap reasoning from the alert record instead of guessing. Correct T-SQL TOP usage in the heaviest query. 2–6× faster than the current default (76 s lookup / 150 s investigation vs 152–451 s). Misses the 30 s / 45 s targets, but no faster model is currently accurate. Costs: reasoning-token-heavy (748 on a greeting; 1,560 on the investigation). |
| **2** | **nvidia/nemotron-3.5-lightning-30b-a3b** | The **fast accurate-lookup** candidate: 9.3 s lookup (meets target), ~15 s chat, thinking disableable (0.43 s probe). **Failed the investigation twice over**: misread its own SELECT columns (humidity as temperature) and used no-TOP queries. Worth a remediation gate (narrow-column guidance in the tool docstring is NOT needed — the docstring already lists columns; a prompt-level instruction or thinking-off retest may fix it). Not adoptable as-is. |
| **3** | **nvidia/nemotron-3.5-bolt-e4b** | Smashes every latency target (2.6 / 3.3 / 5.8 s) and passes lookup — but its SQL dialect (`LIMIT`) is **hard-blocked by the production guard**, and it is a shallow investigator that confidently answers "no excursions" without checking. Candidate for ultra-fast simple lookups only, after a dialect fix and accuracy remediation. Not for investigations. |
| **4** | deepseek v4.1-flash **thinking-off** | The earlier benchmark's "biggest lever" is now **measured as an accuracy hazard**: TTFT improves to 8.5 s, but loop speed barely changes (156 s lookup) and it failed the investigation (claimed no excursions from an unfiltered sample). Not recommended. |
| **5** | deepseek v4.1-flash **default** (current) | Slowest, and now **two reliability failures in two sessions** (timeout storm on 2026-11-08 morning; HTTP 500 this session) plus a reasoning leak in streaming. Accurate when it completes, but 152–451 s per task with ~7% hard-failure rate observed is not a viable interactive primary. |

**No model currently meets all three latency targets with correct
answers.** The choice the data forces is: accuracy-first
(**glm-5.3-flash**, correct, 2–6× faster than today) or speed-first with
remediation (**lightning**, meets chat/lookup targets, investigation
accuracy broken, likely fixable).

## 5. Recommended decision (for review — nothing changed in production)

**Adopt `z-ai/glm-5.3-flash` as the primary candidate model**, subject to
gates before any production change:

- **G-1 Soak test:** repeat the full battery (lookup, excursion, plus
  SOP/weather scenarios) 3× to confirm stability and the accuracy margin —
  this session's numbers are single-run.
- **G-2 Guard-adaptation test:** rerun with the REAL `sql_guard.validate_query`
  wired into the fixture so guard rejections and model retries are measured,
  not simulated away.
- **G-3 Cost check:** glm's reasoning appetite (748–1,560 reasoning tokens
  per task) is 5–10× bolt's usage; estimate spend at expected chat volume.
- **G-4 Streaming leak filter:** if streaming is ever adopted, `reasoning_content`
  deltas must be filtered (present in glm, lightning, deepseek default).
- Keep **lightning-30b** as the designated fast-path candidate and run its
  remediation experiment (thinking-off + retest investigation accuracy);
  keep **bolt-e4b** for a possible dedicated simple-lookup path only.

## 6. Unresolved limitations

- Single run per test per config; serving variance (±2× on identical inputs
  was observed earlier on DeepSeek) is not captured by n=1.
- Fixtures simulate SQL execution (~0 ms); real SQL Server adds latency and
  real result shapes; the guard was NOT enforced during the loops (§3d).
- `openai/gpt-oss-20b` timed out once — could be transient; re-probe before
  ruling it out.
- Rate limits/credits: NIM exposes no credit API (F-4); token counts above
  are the only usage signal; 40 RPM org-wide remains the ceiling.
- Weather/SOP tools not exercised in this comparison (out of the three-test
  scope); glm's alert-based reasoning suggests good behavior, but SOP
  citation quality is untested.

**Stopped here for architecture review and approval, as instructed.** No
provider change, no default-model change, no infrastructure, no security
weakening. Production config is untouched: `deepseek-ai/deepseek-v4.1-flash`.
