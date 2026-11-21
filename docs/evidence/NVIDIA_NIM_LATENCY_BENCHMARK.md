# NVIDIA NIM DeepSeek V4.1 Flash — Latency Benchmark and Optimization Report

> **🆕 CORRECTION NOTE (2026-11-08, see [`SHIPMENT_ACCURACY_AUDIT.md`](SHIPMENT_ACCURACY_AUDIT.md)):**
> the SQL fixtures used by this benchmark contained fabricated high-side
> temperature rows (9.2/8.9 °C) caused by a misreading of the real alert
> ("Observed 1.2 C" is the observed temperature, not an offset). **All
> LATENCY measurements in this report remain valid** (every LLM turn was
> live); **answer CONTENT is invalidated** — the real dataset has zero
> readings above 8.0 °C and one low-side excursion (1.2 °C at 23:45:09).
> Content-corrected measurements with programmatic acceptance checks are in
> [`NVIDIA_NIM_MODEL_COMPARISON.md`](NVIDIA_NIM_MODEL_COMPARISON.md).

Date: 2026-11-08 · Scope: Phase 0 latency investigation (operator-commissioned,
post-R6-1). No provider, model, or infrastructure change was made; no
application source was modified **during** benchmarking (one post-benchmark
defect fix to code introduced earlier the same day is disclosed in §5).
Evidence data: [`NVIDIA_NIM_LATENCY_RESULTS.json`](NVIDIA_NIM_LATENCY_RESULTS.json)
· Rerunnable harness: `architecture-review/nim_latency_benchmark.py`,
`architecture-review/nim_followup_probe.py`.

## 1. Test environment and model configuration

- Endpoint: `https://integrate.api.nvidia.com/v1` (live, operator key, never
  displayed or logged; all recorded outputs secret-redacted).
- Model: `deepseek-ai/deepseek-v4.1-flash` — the production runtime config,
  imported directly from `src.orchestrator.create_llm()` (timeout 180 s,
  max_retries 2, max_tokens 4096, temperature 0).
- Agent: a graph mirroring `fde_agent` exactly (same system prompt, same
  three tool schemas, same recursion_limit 20) with per-turn instrumentation.
- **Honesty labels:** all LLM turns, loop structure and token usage are REAL
  live measurements. The SQL tool is a fixture returning rows built from the
  REAL synthetic dataset (shipment UC-003241, incl. its real alert), ~0 ms —
  real SQL Server latency is NOT measured (Phase 2 will). The SOP tool is a
  fixture from the real `Cold_Chain_Incident_SOP_v2.md` excerpt (Pinecone
  unavailable). The weather tool is a REAL live Open-Meteo call. The FastAPI
  gateway and LibreChat were NOT exercised.
- Budget: ~34 sequential requests total (org limit 40 RPM never approached;
  sequential ReAct loops run at ≈1 request/min).
- Baseline per-turn reasoning-token breakdown was not captured: the harness
  read the wrong LangChain metadata key (see §5). Corrected after the run;
  reasoning share figures come from the direct-API and follow-up probes.

## 2. Baseline results (T1–T6, full ReAct loops)

| Test | Total s | LLM turns | Tool calls | LLM wait s | Tool exec s | In tok | Out tok | Outcome |
|---|---|---|---|---|---|---|---|---|
| T1 greeting (no tools) | **107.2** | 1 | 0 | 107.2 | 0 | 974 | 559 | complete, 2,079-char answer |
| T2 shipment lookup | **216.1** | 2 | 1 | 216.1 | 0.0 | 2,147 | 386 | complete, correct |
| T3 temperature excursions | **365.7** | 4 | 5 | 365.7 | 0.0 | 6,808 | 2,280 | complete, correct, cites readings |
| T4 delivery delays (SQL) | **867.7** | 3 + timeout | 9 | 324.3 | 1.6 | 5,306 | 1,707 | **TIMEOUT** — final turn hit 180 s limit ×3 attempts (≈540 s dead time), `OpenAITimeoutError`, no answer delivered |
| T5 risk (SQL + SOP) | **522.1** | 3 | 11 | 520.7 | 1.3 | 6,101 | 3,626 | complete; final turn 300.6 s (one 180 s timeout + successful retry inside the same turn) |
| T6 conditions (SQL + weather) | **260.1** | 4 | 10 | 258.9 | 1.2 | 8,343 | 2,448 | complete; live weather call 1.2 s |

**Per-turn latency: 52.9–300.6 s observed.** First turns (near-identical
~975-token input) varied 76–144 s across tests — identical-size requests vary
by ~2×, pointing at serving-side variance, not just prompt size. Effective
generation rate ranged 1.2–18 output tokens/s (outliers both ways).

**Every performance target is missed by 3–7×** (§4). A simple greeting —
the cheapest possible interaction — takes **107 s**.

Answer quality note (functional, not latency): all completed tasks were
accurate, well-structured, provenance-disciplined ("synthetic test data"
labeled), and the model never attempted a disallowed query (guard never
triggered; only SELECTs on allowed views were emitted).

## 3. Optimization experiments

### A. Streaming — supported, but TTFT is reasoning-gated and reasoning leaks

- `stream=true` works against the model: 24 content chunks, clean `stop`.
- **Time to first CONTENT chunk: 55.6 s** of a 63.8 s total (max_tokens 4096).
  The stream carries the model's reasoning phase first — first visible answer
  token arrives only after reasoning completes. Target "<5 s first visible
  streamed response" is **not met** (55.6 s).
- **Total completion time is NOT reduced by streaming** (63.8 s streamed vs
  36.8–59.1 s for comparable non-streamed completions) — measured, not assumed.
- **Security finding: `reasoning_content` deltas ARE present in the raw
  stream** (leak check positive). The gateway must filter these deltas
  explicitly before forwarding to LibreChat; forwarding the raw stream would
  expose internal reasoning traces.
- Feasibility: genuine incremental forwarding IS implementable in the current
  FastAPI gateway (the SSE scaffold exists; today it buffers — a code-read
  conclusion, the gateway was not exercised). Streaming only pays off in
  perceived latency **after** reasoning is disabled (§C) — that combination is
  UNMEASURED.

### B. Token limits — caps do not drive latency; F-3 risk persists at low caps

| max_tokens | Total s | finish | Content chars | Reasoning tok | Out tok |
|---|---|---|---|---|---|
| 1024 | 59.1 | stop | 1,060 | 107 | 391 |
| 2048 | 36.8 | stop | 409 | 111 | 212 |
| 4096 | 52.8 | stop | 381 | 117 | 214 |

- No truncation or empty-content failure at any cap ≥1024 on the detail
  question. Latency differences across caps are noise — **the cap is not a
  latency lever**; the 4096 default is safe and should be kept.
- The F-3 failure mode remains real for reasoning-heavy prompts at tight
  caps: the minimal-prompt probe (§E) burned all 1024 output tokens on
  reasoning and returned **zero content** (`finish_reason=length`). Do not
  lower `NVIDIA_MAX_OUTPUT_TOKENS` below 1024; artifact observed at 32
  tokens on 2026-11-03 and at 1024 for reasoning-heavy asks.
- Probe artifact (labeled): these single-turn probes ran WITHOUT bound tools,
  and the model emitted its internal tool-call markup (`<｜DSML｜ …>`) as
  content — the latency/usage numbers remain valid, the content does not.

### C. Reasoning configuration — the one validated, high-impact lever

| Probe | Accepted? | Total s | Reasoning tok | Content |
|---|---|---|---|---|
| `extra_body={"chat_template_kwargs": {"thinking": false}}` | **YES** | **45.2** | **0** | clean 296-char direct answer |
| `extra_body={"reasoning_effort": "low"}` | accepted, no effect | 93.0 | 194 | 277 chars |

- **Disabling thinking via `chat_template_kwargs.thinking=false` is accepted
  by the endpoint and eliminates reasoning entirely** — reasoning tokens 0,
  and a one-sentence answer dropped to 45.2 s (vs 59–94 s reasoning-enabled
  equivalents; vs 84.8 s for the same question with thinking on in the
  follow-up probe — ≈1.9× measured on like-for-like prompts).
- **Caveats (gates before adoption):** (1) NOT yet tested in a full tool
  loop — tool-calling accuracy with thinking off must be re-verified end to
  end; (2) it is an undocumented NIM serving parameter (worked live, but is
  not in the official OpenAI parameter set — verify stability across NIM
  updates); (3) even with thinking off, a trivial turn is still ~45 s
  (§10) — this lever is necessary but not sufficient for the targets.

### D. LangGraph efficiency — structure sound; waste is in repeated tool calls

- 2–4 model turns per task; recursion_limit 20 never approached; no runaway
  loops. Tool calls are batched in parallel (4–6 calls in one turn observed).
- **Waste found (measured):** T5 called `search_compliance_sop` **three
  times** with the identical 678-char result re-entering context each time;
  T4/T5/T6 re-queried views that had already returned "No records matched"
  (5 repeats, 38 chars each). Note: the empty-view repeats are partly a
  fixture artifact (VW_VEHICLES/ROUTES/SENSORS were not fixtured); the
  duplicate SOP calls are genuine model behavior.
- Re-sent history is the context-growth driver: input tokens grow ~975 →
  ~2,900 over 4 turns. Unavoidable in a stateless API; bounded by the
  gateway's 60k-char cap.

### E. Prompt efficiency — measured, not a factor

- The production system prompt is tiny: 313 input tokens with it, 53
  without. Full-prompt turn 171.8 s vs minimal-prompt 161.8 s — a ~10 s
  difference inside observed turn-variance (±35 s). **Prompt size is not
  the problem; do not spend effort shortening it.**
- The minimal-prompt probe also re-demonstrated F-3 (all 1024 tokens →
  reasoning, 0 content chars).

### F. Alternative NVIDIA models — no data obtainable; honest limitation

- `GET /v1/models` lists 84 models (unauthenticated — per finding F-2 this
  list is not credential proof and, it turns out, not a servability list
  either).
- **Five catalog-listed instruct models were probed and ALL returned HTTP
  404** at the chat endpoint (harness picks `ai21labs/jamba-1.5-large-instruct`,
  `aisingapore/sea-lion-7b-instruct`; follow-up picks `meta/llama-3.2-90b-vision-instruct`,
  `microsoft/phi-3-vision-128k-instruct`, `microsoft/phi-3.5-moe-instruct`).
  The public catalog apparently includes non-servable entries; the
  authoritative listing lives in the operator's build.nvidia.com dashboard.
- **No alternative-model latency comparison is possible from this
  environment.** A proper survey requires the behind-login catalog to pick
  real candidates, then 2–3 single-turn probes each. Deferred as a
  recommended follow-up — potentially the highest-impact open lever.

## 4. Comparison tables

**Targets vs measured (tool-using agent, full ReAct loop):**

| Operation | Target | Measured | Verdict |
|---|---|---|---|
| Simple chat | <15 s | 107.2 s | ❌ 7× over |
| Shipment lookup | <30 s | 216.1 s | ❌ 7× over |
| Single SQL investigation | <45 s | 365.7 s (T3) | ❌ 8× over |
| Multi-tool investigation | <90 s | 522.1 s (T5) / 260.1 s (T6) | ❌ 3–6× over |
| First visible streamed response | <5 s | 55.6 s (first content chunk) | ❌ 11× over |
| (Delivery delays, T4) | — | 867.7 s then NO ANSWER (timeout) | ❌ reliability failure |

**Optimization levers, measured effect:**

| Lever | Effect | Confidence |
|---|---|---|
| `thinking=false` | ≈1.9× on single turns (94→45 s like-for-like); removes reasoning phase | Measured, single-turn only — tool loop UNTESTED |
| Token caps 1024–4096 | none on latency (noise) | Measured |
| Prompt shortening | none (~10 s, within variance) | Measured |
| Streaming | no total-time gain; TTFT 55.6 s (reasoning-gated); leak found | Measured |
| Tool-loop dedupe | T5: ~2,000 wasted context tokens (SOP ×3); unknown latency share | Partially measured |
| Alternative model | unknown — catalog 404s | No data |

## 5. Token usage and reasoning-token analysis

- **Where reasoning lives (verified live):** LangChain exposes it as
  `usage_metadata.output_token_details.reasoning` (follow-up probe: 67 of
  105 output tokens on a one-sentence question). The raw API reports it as
  `usage.completion_tokens_details.reasoning_tokens`.
- **Reasoning share of the output budget: 27–100%** across probes
  (107/391, 111/212, 117/214, 600/1024, 67/105 — and 1024/1024 on the
  reasoning-heavy minimal-prompt probe). Reasoning is not a fixed overhead;
  it scales with task complexity.
- Baseline input tokens grow with re-sent history (974 → ~2,900 by turn 4);
  output per turn ranged 97–2,714 tokens.
- **Harness defect (disclosed):** the baseline read
  `output_tokens_details.reasoning_tokens` — the wrong LangChain key — so
  per-turn baseline reasoning shows 0 in the JSON. Corrected in the harness
  post-run; baseline reasoning breakdown was not re-measured (budget).
- **Application defect found and fixed (post-benchmark, disclosed):** the
  C12 token counter added to `api/main.py` on 2026-11-08 (R6-1) used the
  same wrong key and would have logged `reasoning_tokens: 0` on every
  request. One-line fix applied after benchmarking completed; offline suite
  re-run green (45/45). Totals were always correct; only the reasoning
  breakdown was affected.

## 6. Streaming feasibility (summary)

Technically feasible end to end: model streams, chunks flow, the gateway's
SSE scaffold can be converted to incremental forwarding. Two hard
preconditions: (1) **filter `reasoning_content` deltas** — measured present
in the raw stream, must never reach LibreChat; (2) it only improves perceived
latency if combined with `thinking=false` (TTFT is otherwise reasoning-gated
at ~55 s — measured). Streaming does not reduce total inference time
(measured). No gateway changes were made (restriction); this is a design
conclusion for the review.

## 7. LangGraph performance findings (summary)

Turn counts are healthy (2–4 per task; no recursion blowups; parallel tool
batching works). Measured waste: duplicate identical tool calls (SOP ×3 in
T5) and repeated queries after empty results. Cheap, low-risk fixes for a
future phase: dedupe identical `(tool, args)` pairs within a turn; on a
second consecutive empty result for the same view, return an explicit
"already queried — no data" message instead of identical context. Both are
agent-plumbing changes, not guard/security changes; none were applied
(restriction).

## 8. Faster NVIDIA model alternatives (summary)

No comparison data — 5/5 catalog-listed candidates 404'd at the chat
endpoint. The public `/v1/models` list demonstrably includes non-servable
entries. **Follow-up recommended:** operator pulls the servable model list
from the build.nvidia.com dashboard (behind login), we shortlist 2–3
tool-calling instruct models, and probe each with (a) one trivial single
turn, (b) one tool-calling turn — before any model decision. This stays
inside the R6-1 provider decision (NVIDIA NIM hosting) and requires no
runtime change to test.

## 9. Recommended improvements, ranked by impact and implementation risk

1. **Disable thinking for the conversational runtime**
   (`chat_template_kwargs.thinking=false`). Impact: **HIGH** — the only
   measured >1.5× lever; removes the reasoning phase that gates both total
   time and streaming TTFT. Risk: **MEDIUM** — must pass a tool-loop accuracy
   gate first (one full ReAct run + answer-quality check with thinking off),
   and it is an undocumented serving parameter (add a startup probe or
   version-pin check).
2. **Rework the request timeout/retry policy.** Impact: **HIGH**
   (reliability): T4 burned ~540 s in timeout retries and delivered nothing;
   a 9-minute 502 is the worst possible UX. With thinking off, turns shrink;
   set timeout ~120–150 s and `max_retries=1` (fail fast, clear error), or
   keep 180 s only if thinking stays on. Risk: **LOW** (config values).
3. **Fix the reasoning-token accounting key** — Impact: correctness of the
   C12 metric; Risk: none. **DONE post-benchmark** (§5), suite green.
4. **Tool-loop dedupe + empty-result short-circuit** (§7). Impact: **MEDIUM**
   (context bloat ~2k tokens observed; second-order latency). Risk: **LOW**.
5. **Streaming adoption** — only together with #1; must filter
   `reasoning_content`. Impact: **MEDIUM** (perceived latency only; total
   time unchanged — measured). Risk: **MEDIUM** (gateway SSE rework + leak
   filtering + LibreChat client behavior).
6. **Servable-model survey** (§8). Impact: **UNKNOWN, potentially the
   decisive lever** — every other optimization is a constant-factor; a
   genuinely faster model is the only path that could approach the <15 s
   chat target. Risk: **NONE to investigate** (2–3 probe requests).
7. **Prompt shortening — REJECTED** (measured no-effect, §E).

## 10. Remaining limitations and the explicit suitability recommendation

**Limitations:** fixture tools (real SQL Server/Pinecone latency unmeasured —
production answers will be slightly slower than benchmark); gateway and
LibreChat not exercised; baseline per-turn reasoning breakdown lost to the
harness key defect (probe evidence used instead); alternative-model survey
blocked by catalog 404s; single benchmark session (NIM serving variance
means absolute numbers carry ±~35 s/turn uncertainty); T4 never delivered an
answer, so its "delays" task latency is a lower bound.

**Recommendation — is DeepSeek V4.1 Flash suitable as the primary
conversational model for this logistics application?**

**No — not for interactive conversational operations as currently served.**
Every performance target is missed by 3–7×, and the cheapest possible
interaction (a greeting) takes 107 s; even with thinking disabled — the
strongest validated optimization — a trivial turn still costs ~45 s against
a <15 s target, and tool-using answers would remain multi-minute. The model
is *functionally* excellent for this domain (accurate, provenance-disciplined,
guard-respecting on all six tasks), but NVIDIA NIM's serving of it is
consistently too slow for routine dispatch use, and its reasoning-first
design cannot deliver a <5 s first visible streamed response.

**Recommended posture for the architecture review:**
1. Keep the R6-1 provider decision (NVIDIA NIM) — out of this benchmark's
   scope either way — but treat the *model choice within NIM* as open: run
   the servable-model survey (§8) before any further latency investment.
2. If DeepSeek V4.1 Flash is retained: adopt `thinking=false` after its
   tool-loop gate, rework timeouts (fail fast), add tool-loop dedupe, and
   redesign the UX around long waits (progress indication or asynchronous
   answer delivery) rather than pretending sub-minute responses are possible.
3. Record that streaming is a perceived-latency tool only (measured), gated
   behind reasoning removal and a reasoning-content leak filter.

The benchmark is complete; this report stops here for architecture review as
instructed. No provider, model, infrastructure, or security change was made.
