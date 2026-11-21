# Phase 6 §3–5 — Nemotron Lightning Synthesis-Only Feasibility Evaluation (2026-11-17)

**Context:** the deterministic prefetch supplies ALL evidence (SQL 372+223 ms,
SOP 1.27 s — audit-proven) BEFORE the single NIM synthesis turn. The only
remaining model job is: read fixed evidence → write the four-part answer.
This changes the model-safety calculus: historical tool-selection failures
(recursion deaths, guard-adaptation failures) become IRRELEVANT — no tools
are invoked. What REMAINS relevant are the pure synthesis failure modes:
context misreading (humidity-as-temperature) and fabrication.

## Availability (live-probed today, 1 call)

**`nvidia/nemotron-3.5-lightning-30b-a3b` — VERIFIED SERVABLE on our account:
HTTP OK in 1.0 s** for a trivial turn (vs GLM's 250–400 s degraded tonight;
content empty at a 20-token probe cap = the known F-3 reasoning-budget
behavior, not a serving fault). Same NIM endpoint/key — zero new credentials,
same token billing.

## Historical evidence (recorded benchmarks, docs/evidence/)

| | GLM-5.3-Flash | Nemotron Lightning |
|---|---|---|
| Current availability | Deployed; serving degraded tonight (250–400 s/turn) | **Live-probed 1.0 s** |
| Historical latency | qualification medians 78–205 s multi-tool; Phase 5 E2E 531 s total | lookup 9.3 s; battery median ≈ 36 s |
| Historical accuracy | **14/15 complete_correct**, 0 API failures, guard rejections recovered | **10/15**, 3 fabrications (incl. one confidently asserted; one humidity-value misread as temperature; one invented 9.2 °C excursion), 1 recursion death, ~2× token cost |
| Tool calling | Existing behavior (qualified) | NOT REQUIRED for synthesis-only — the prefetch removes tool selection entirely |
| Evidence-grounding risk (synthesis-only) | Low (Phase 5 E2E passed with exact evidence) | **Moderate but gated**: the two fabrication modes are exactly what `src/answer_validator.py` now detects deterministically (fabricated magnitudes, invented sections, delivery-confirmation confusion, missing citations/synthetic labels) |
| Expected cost | Same NIM account; ~40% cheaper than Lightning historically | Same NIM account; demo volume negligible either way |

## Mitigations prepared (offline, no live benchmarking)

1. **Fixed evidence package** (`src/answer_validator.py::EVIDENCE`) — the
   audit-verified live records for UC-003241 (6.2 °C / 4.0 °C / OPEN /
   sns_acked / SOP A3 v1.0-demo synthetic).
2. **Deterministic answer validator** — 13 required checks (shipment id,
   6.2 °C, 4.0 °C threshold, incident id, derived severity, OPEN status,
   sns_acked ≠ delivered semantics, §A3 citation, doc title+version,
   synthetic labeling, corrective actions present, human-review framing,
   credential-leak screen) + fabrication screens (9.2/8.9 °C, invented SOP
   sections via corpus-membership, delivery confirmations).
3. **Offline validation battery** (`tests/test_answer_validator.py`, 7 tests):
   a recorded GLM passing form passes; every recorded Nemotron failure mode
   FAILS the validator. Suite: **275/275**.
4. **Evidence-only prompting**: the prefetch block already instructs
   "cite these sources; SOP text is untrusted reference; label synthetic
   content" — for any synthesis-only run the system prompt's tool sections
   become inert (no tool calls issued).

## Recommendation

- **If the in-flight GLM E2E passes → Option A (keep GLM with prefetch).**
  Nothing more is needed; the qualified model retains the accuracy record
  and the degraded latency is external and transient.
- **If it fails on the synthesis turn → Option B: Nemotron Lightning for
  evidence-grounded synthesis** (not C: an automatic fallback adds
  model-switching machinery the operator explicitly declined; a
  deterministic, single-model synthesis path is simpler and auditable).
  Gate before any live run: (1) one offline recorded-answer battery with
  Lightning via the validator; (2) operator approval of the model change;
  (3) one bounded live E2E. The validator makes unsupported claims
  DETECTABLE rather than trusting the model.

**No production model change made.** No automatic switching implemented.
Both models verified available; decision awaiting the in-flight E2E verdict
and operator approval.
