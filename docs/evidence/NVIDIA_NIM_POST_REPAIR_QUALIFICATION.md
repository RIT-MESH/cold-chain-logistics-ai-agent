# NVIDIA NIM Post-Repair Final Model Qualification

Date: 2026-11-09 · The decisive qualification after the accepted SQL tool
repair: both candidates through the full 5-scenario × 3-repetition battery
with the repaired production tool interface, the unchanged real SQL guard,
dataset-derived execution, and the finalized validation checks.
**Production default unchanged: `deepseek-ai/deepseek-v4.1-flash`. No AWS, no
architecture change.** Evidence: [`NVIDIA_NIM_POST_REPAIR_QUALIFICATION.json`](NVIDIA_NIM_POST_REPAIR_QUALIFICATION.json)
(complete answers stored in full, secret-redacted; every original verdict
preserved alongside the corrected one with explicit reasons) · Harness:
`architecture-review/nim_post_repair_qualification.py` · Suite: **92/92**.

## 1. Validation-evidence discrepancy — resolved (Task 1)

The repair-validation report marked lightning's excursion `complete_correct`
while the JSON's recomputed field said `partial`. Root cause: that
reclassification ran the checker on a 600-char **excerpt**; lightning's
full-answer low-side identification was recorded True but invisible to the
excerpt recompute. Resolution in this qualification, by construction:
**complete answers are stored and the finalized checker runs only on the
full response.** Where this qualification's own first-pass verdicts needed
correction (two humidity false positives, §5), original AND corrected
verdicts are both preserved in the JSON with per-conversation reasons.
**No partially verified response is classified complete** — the complete
class requires the acceptance checks AND successful tool execution.

## 2. Security and retry verification (Task 4)

Tool-level recovery test: a deliberately invalid query sequence drives the
**PRODUCTION** tool's rejection budget against the **REAL guard** (no LLM,
no API cost). Recorded in the JSON (`recovery_verification`):

| Step | Result |
|---|---|
| Unqualified view, no TOP | blocked: "Only FDE_VIEWS approved unified views may be queried" |
| Exact duplicate | **budget intervention**: "already rejected… Do not repeat it" |
| Corrected query (prefix + TOP) | **guard accepts** → production execution path |
| LIMIT variant | blocked: "Invalid SQL syntax" |
| DELETE attempt | blocked: "Only one SELECT statement is allowed" |
| Distinct invalids 4–5 | **budget exhausted**: "Stop issuing queries and answer only from the evidence already retrieved" |
| Post-exhaustion attempt | same terminal instruction |

The guard is unchanged and never bypassed; no SQL is rewritten. Across the
30 battery conversations: **glm 2 guard rejections (both recovered), lightning
3 (all recovered); zero budget interventions needed** — the repaired
documentation prevents the rejection storms entirely.

## 3. Battery results (Tasks 2 & 3) — corrected verdicts on full answers

**`z-ai/glm-5.3-flash` — 15 conversations**

| Scenario | Verdicts (3 reps) | Times (s) |
|---|---|---|
| lookup | ✅✅✅ | 8.8 / 13.0 / 9.6 |
| excursion | ❌ / ✅ / ✅ | 55.0 / 77.8 / 100.8 |
| delay | ✅✅✅ (correct 8-min / 01:45 claims, verified) | 104.8 / 155.0 / 205.4 |
| sop | ✅ / ✅ / ✅ (r1 corrected — see §5) | 240.5 / 101.9 / 169.2 |
| weather | ✅ / ✅ / ✅ (r1 corrected — see §5) | 73.5 / 127.2 / 210.3 |
| **Total** | **14 complete_correct · 1 incorrect · 0 errors** | median 101.9 · max 240.5 |

**`nvidia/nemotron-3.5-lightning-30b-a3b` — 15 conversations**

| Scenario | Verdicts (3 reps) | Times (s) |
|---|---|---|
| lookup | ✅✅✅ | 6.1 / 7.0 / 4.6 |
| excursion | ✅✅✅ (2 with 1 guard rejection each, recovered) | 61.8 / 30.8 / 28.9 |
| delay | ✅ / ⚠ recursion error / ❌ | 83.2 / 26.5 / 56.4 |
| sop | ✅✅❌ | 37.2 / 30.1 / 38.8 |
| weather | ⚠ partial / ✅ / ❌ | 39.7 / 44.9 / 34.6 |
| **Total** | **10 complete_correct · 3 incorrect · 1 partial · 1 API/recursion failure** | median 35.9 · max 83.2 |

Accuracy gates enforced per §3: shipment identity, cargo-specific limits
(2.0/8.0), observed temperatures and humidity against records, the known
excursion (1.2 °C LOW at 23:45:09), actual-vs-expected delivery times,
SOP applicability (cargo-specific — the 0.0–4.0 fresh-perishables SOP does
not apply to insulin), live-weather provenance (weather-tool result required),
no fabricated measurements, no unsupported completeness claims, and
**successful tool execution required for any complete verdict**.

## 4. Failure analysis (the substance of this qualification)

**F-A — The alert-message ambiguity (root cause of every temperature
fabrication in this battery, on BOTH models).** The dataset's alert message
reads "Observed 1.2 C outside configured 2 to 8 C". Multiple strong models —
including glm — misparse it as "the reading deviated by 1.2 degrees from the
band", deriving candidate magnitudes **0.8 °C (2.0 − 1.2) or 9.2 °C (8.0 + 1.2)**:
- glm excursion r1 (incorrect): explicitly hedged — "'1.2 C outside' could
  mean 0.8 °C (freeze-side) or 9.2 °C (warm-side); the alert message doesn't
  specify direction". Honest about limits, but it had sampled only the
  chronological TOP-10 (the 1.2 °C row is the 14th of 16) and did not run the
  out-of-band filtered query — so it speculated instead of retrieving.
- lightning delay r3 (incorrect): asserted confidently — "deviated by
  +1.2 °C above the upper limit (i.e., ≥ 9.2 °C)".
- lightning sop r3 (incorrect): "likely < 0.8 °C or > 9.2 °C".
- (This is the same misreading the original benchmark fixture author made —
  the message is genuinely ambiguous to careful readers.)
**Minimum corrective action (data-side, for operator approval): reword the
alert message in the synthetic dataset to an unambiguous form**, e.g.
"Observed temperature 1.2 C, below configured range 2 to 8 C", plus a prompt
nudge to always run the out-of-band filter before interpreting an alert.

**F-B — Success-loop recursion (lightning delay r2).** Zero guard blocks —
lightning looped on **successful duplicate queries** (9 × ok) until the
recursion limit. The rejection budget only meters rejections. **Minimum
corrective action (code, follow-up): meter repeated identical queries
regardless of guard outcome.**

**F-C — Checker false positives found and fixed during this run (both
regression-tested, suite 92/92):** real `fuel_level_pct` values (94.54,
47.66) cited near humidity mentions were falsely flagged by the
window heuristic; fixed with a nearest-preceding-label rule. The two affected
glm conversations were reclassified on their full answers (originals
preserved). All other cited numbers across all 30 conversations were verified
database-exact, including lightning's real humidity 69 % at excursion time.

**F-D — lightning weather r3 (incorrect):** quoted a fabricated "temperature
drift > 1 °C" threshold that exists neither in the SOP nor the data.

## 5. Performance and cost (Task 5) — measured, not assumed

| Metric | glm-5.3-flash | lightning-30b |
|---|---|---|
| Median / max response time | 101.9 s / 240.5 s | 35.9 s / 83.2 s |
| Complete-correct rate | **14/15 (93%)** | 10/15 (67%) |
| API/recursion failures | **0** | 1 |
| LLM turns (total) | 58 | 113 |
| Successful SQL queries | 73 | 74 |
| Guard rejections (all recovered) | 2 | 3 |
| Input / output / reasoning tokens | 140,478 / 49,370 / 29,973 | 302,073 / 45,862 / 24,106 |
| Total tokens (battery) | **≈ 220k** | ≈ 372k |
| Estimated cost | **NIM exposes no pricing/credit API (finding F-4) — cost is reported as measured token consumption; glm used ~40% fewer tokens for identical work** |

Total live spend this qualification: ~171 model requests (sequential; the
40 RPM org-wide limit never approached), plus the zero-cost tool-level
recovery test. Latency targets: lightning met all per-scenario targets;
glm met the lookup target (<30 s) but medians for multi-tool scenarios run
78–205 s against the aspirational <45–90 s targets.

## 6. Recommendation (Task 6)

**`z-ai/glm-5.3-flash` qualifies; `nvidia/nemotron-3.5-lightning-30b-a3b`
does not.** Accuracy and reliability are mandatory, and low latency alone is
insufficient: lightning is 3× faster but failed the consistency bar — 3
incorrect outcomes including a confidently asserted fabrication, 1 recursion
failure, 1 partial, and 2× the token cost. glm: 14/15 database-exact
completions, zero API failures, every guard rejection recovered, and its
single failure was an honestly-hedged ambiguity it could have resolved with
one more filtered query.

**Explicit approval recommendation: adopt `z-ai/glm-5.3-flash` as the
production runtime model within NVIDIA NIM (R6-1 provider unchanged)**,
subject to operator approval and the standing blueprint amendment, with:
1. The F-A data fix (alert-message rewording) applied to remove the last
   known accuracy hazard — recommended before production;
2. The F-B recovery-budget extension (meter repeated successful queries)
   as a small follow-up code change;
3. UX expectations set for glm's measured multi-tool latencies (78–205 s
   medians) — the interactive-target question is an operator trade-off,
   not a qualification blocker.

If the operator declines glm on latency grounds, the honest statement is:
no candidate currently combines glm's accuracy with lightning's speed;
the alternatives are accepting glm's latency, restricting lightning to
lookup-class interactions with remediation, or resuming the model search.

**Stopped for architecture review. Production default model unchanged.**
