# Shipment UC-003241 Factual-Consistency Audit

Date: 2026-11-08 · Trigger: three benchmark responses contradicted each other
about the same shipment. This audit compares every claim against the actual
unified dataset records, identifies the root cause, and installs objective
acceptance checks so a fluent answer cannot pass while contradicting the
database.

## 1. The conflicting claims (latency benchmark, 2026-11-08)

| Source | Claim |
|---|---|
| T3 (excursion investigation) | "**two readings above its configured upper limit of 8.0 °C**, both in the early hours of 2026-07-07; one generated a system alert" |
| T5 (risk investigation) | "**one confirmed temperature excursion above the shipment's configured 8.0 °C ceiling (peak 9.2 °C)**" |
| T6 (route conditions) | "The shipment **stayed within its configured 2–8 °C band throughout the observed window**, but one derived temperature alert fired" |

All three describe the same synthetic shipment (UC-003241, insulin pens,
band 2.0–8.0 °C). At most one of them can be true.

## 2. Ground truth — the actual unified dataset records

Queried directly from `data/unified/unified_cold_chain.sqlite` (offline,
no LLM involved):

- **16 telemetry rows** exist for UC-003241 (2026-07-06 17:15 → 2026-07-07 00:45).
- **Readings above 8.0 °C: ZERO.** The maximum observed temperature in the
  entire trip is **7.8 °C** (2026-07-06 19:45:09).
- **Readings outside the 2.0–8.0 °C band: EXACTLY ONE — 1.2 °C at
  2026-07-06 23:45:09 (LOW side, below the 2.0 °C minimum; risk level
  "high")**. This is the only excursion in the dataset.
- **One alert row**: `temperature_excursion`, severity medium, at
  2026-07-06 23:45:09, message "**Observed 1.2 C outside configured 2 to 8 C**"
  — i.e., the observed temperature *was* 1.2 °C.
- Shipment record: insulin pens, band 2.0–8.0 °C, receiver verdict
  "accepted", arrived 01:45:09 vs expected 01:37:09 (8 minutes late).

**Verdict: all three benchmark claims are wrong.** There were never any
high-side readings, and there is exactly one low-side excursion.

## 3. Root-cause analysis

### 3a. Primary cause — FIXTURE INCONSISTENCY (harness defect, now fixed)

The latency-benchmark harness hand-built the SQL tool's fixture responses
instead of deriving them from the dataset. Its author misread the alert
message "Observed 1.2 C outside configured 2 to 8 C" as meaning
"**+1.2 °C above the upper limit**" and fabricated a 9.2 °C reading — when
the message actually means the observed temperature **was 1.2 °C**, a
low-side excursion. A second fabricated row (8.9 °C) was added "for
realism". The fixture therefore fed every model **two high-side excursions
that do not exist in the dataset and hid the real low-side one.**

Attribution per response:

- **T3** ("two readings above 8.0 °C"): the model **faithfully reported the
  (wrong) evidence it was given** — 9.2 °C and 8.9 °C were both in its tool
  results. Cause: fixture. LLM interpretation: faithful to context.
- **T5** ("one confirmed excursion, peak 9.2 °C"): same fixture cause. The
  differing count (one vs T3's two) is defensible interpretation of the
  same wrong data (one alert-confirmed event).
- **T6** ("stayed within the 2–8 °C band throughout"): **this one is
  additionally an LLM interpretation failure** — T6 received the SAME fixture
  containing 9.2/8.9 °C rows and still claimed the band was never breached,
  while acknowledging a "derived alert". It contradicted evidence in its own
  context, not just the fixture.

So: inconsistent fixtures explain the T3/T5 divergence from reality; T6
demonstrates that even with consistent evidence, this model instance produced
a fluent answer contradicting its own context — exactly the failure mode the
acceptance checks below exist to catch.

### 3b. Why it went unnoticed

Nothing in the benchmark compared answer CONTENT against the database —
the harness measured latency and eyeballed answers for plausibility, and
all three answers were fluent, well-structured, and cited "evidence".
Fluency masked fabrication.

## 4. Corrections installed

1. **Fixtures are now ground-truth-corrected** in the model-comparison
   harness (`architecture-review/nim_model_comparison.py`) and emulate real
   SQL result shapes:
   - an unfiltered `TOP 10` returns the chronological first 10 rows, which
     do NOT contain the excursion (forcing the model to issue a filtered
     query, as in production);
   - a low-side filtered query returns the single real 1.2 °C row;
   - a high-side filtered query truthfully returns "No records matched".
2. **Objective acceptance checks are now programmatic** and run on every
   candidate model's answers (see `check_answer()` in the harness):
   - lookup test must contain the cargo type (insulin), the band (2.0/8.0),
     the verdict (accepted), and the shipment ID;
   - excursion test must cite 1.2 °C and the 23:45 timestamp, identify the
     excursion as LOW-side, must NOT contain fabricated values (9.2/8.9,
     "above 8.0"), and must NOT claim "no excursion".
   An answer passes only if EVERY check passes — a fluent response that
   contradicts the database fails visibly, with the failing checks listed.
3. **Latency benchmark annotation**: the 2026-11-08 latency benchmark report
   (`NVIDIA_NIM_LATENCY_BENCHMARK.md`) now carries a correction note — its
   LATENCY measurements remain valid (all LLM turns were real), but its
   answer CONTENT is invalidated by the fixture defect and superseded by
   this audit and the corrected comparison.

## 5. Process lessons (recorded, not optional)

- Tool fixtures must be **derived programmatically from the dataset**, never
  hand-written from a human reading of a message field.
- "Answered plausibly" is not an acceptance criterion; every benchmark that
  produces answers needs **database-anchored programmatic checks**.
- The production SQL guard was never at fault and was never bypassed: every
  model query in both benchmarks was a SELECT on allowed views.

## 6. Reproducing this audit

Offline, free, no LLM: run the ground-truth queries in
`architecture-review/_ground_truth_extract.py` (16 telemetry rows, the
outside-band filter, alerts, shipment record) against
`data/unified/unified_cold_chain.sqlite`.
