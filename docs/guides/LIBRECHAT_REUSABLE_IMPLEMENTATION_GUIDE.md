# Building a Reliable AI Chat Assistant Behind LibreChat — a Reusable Engineering Guide

Lessons from wiring LibreChat to a LangGraph + FastAPI agent gateway over an
enterprise-style guarded database (2026–2027 project). Every lesson below was
learned live: each failure was reproduced, root-caused with logs, and fixed
with a regression test.

## 1. LibreChat speaks OpenAI — return OpenAI-shaped errors, always

LibreChat talks to custom endpoints with the OpenAI client library. When the
gateway returned FastAPI's default `{"detail": ...}` error shape, LibreChat
could not parse it and the user saw **nothing at all** — a blank response
with no way to tell a timeout from a crash. Return a top-level error object:

```json
{"error": {"message": "human-readable message", "type": "service_unavailable",
            "param": "", "code": "nvidia_timeout"}}
```

The `message` is what the user sees in the chat UI. Write messages a user can
act on ("The AI service is degraded — shipment questions with an ID are still
answered from the database"), not stack traces.

## 2. Bound your model timeouts, and classify failures

- Give each model path its **own** timeout budget. A fast synthesis model
  (measured 0.7 s/turn) must not inherit a 300 s × 3-retry budget designed
  for a slow reasoner — otherwise one stuck call hangs the request ~15 minutes.
- On failure, walk the exception `__cause__` chain and classify:
  timeout / auth / model-unavailable / application error. Audit the class;
  surface a message that matches the class.
- Bound retries (we allow 2 attempts for the reasoner, 1 for synthesis) and
  never retry inside the HTTP layer.

## 3. Collapse multi-turn tool loops with deterministic prefetch

A ReAct agent needs one LLM turn per tool decision. If your serving
infrastructure is slow, a 3-turn conversation is 3× the timeout risk. For
recognized question shapes, pre-run the tools deterministically:

- Extract the entity (regex `UC-\d{6}`), never free-form user text.
- Run FIXED, guard-valid SQL templates through the SAME guarded tool the
  model uses — so the SQL guard, read-only grants, metering, and audit all
  apply unchanged.
- Inject the verbatim tool output as a system message and answer in ONE
  synthesis turn.

Result: our shipment questions went from 3–5 slow turns (5–15 min, often
timing out) to a single sub-10-second turn.

## 4. Resolve conversation context BEFORE prefetching

Users ask follow-ups like "any problem in this shipment?" after a report.
Rules that worked:

1. An explicit entity in the current message always wins.
2. Referents ("this shipment", "it", "them") resolve from the conversation
   history LibreChat sends with every request — scan backward, most recent
   match first.
3. Two entities + comparative language ⇒ comparison scope; two entities
   without it ⇒ the primary subject.
4. More than two candidates ⇒ do NOT guess: fall back to the stock flow and
   let the model ask for clarification.
5. **No global state.** Resolution from the per-request message list only —
   conversation isolation (and multi-user isolation) is guaranteed by
   construction, and you never have to test for leaks you cannot have.

Trust boundary: resolved ids only ever parameterize FIXED SQL. Conversation
text can influence *which* template runs, never *what* the SQL is.

## 5. Validate answers by intent, never by mention

Our first validator dispatched on "the answer mentions shipment X" — so a
temperature-history answer about X was judged on incident-and-SOP criteria it
was never asked to satisfy, and correctly-written answers were rejected as
502s. Dispatch validation on the **classified intent of the request**:

| Intent | Validator |
|---|---|
| incident + SOP guidance | strict ground-truth checks (exact ids/measurements/citations) |
| comparison | both entities present, no tool syntax, disclosure |
| temperature/humidity history | light checks only |
| general report | no tool syntax, correct entity, synthetic-data disclosure |

Two refinements that matter in production:

- If SOP retrieval failed (embedding service down), the strict validator must
  accept an honest "SOP guidance could not be retrieved" answer — and must
  still reject any fabricated citation.
- Keep a bounded corrective retry: one re-generation on validation failure,
  then a fallback. Our retry alone fixed ~40% of rejections (missing
  disclosure, geo labels); the rest were caught before users ever saw them.

## 6. Enforce the SQL guard in one place — the tool

A single `query_telemetry_db` tool owns validation: schema-qualified
allowlisted views only, `TOP` caps, explicit columns, no joins/CTEs/UNION,
read-only. The model sees exact valid examples in the tool docstring; the
prefetch uses the same fixed queries; nothing else executes SQL. Add metering
so repeated identical queries (success-loop deaths) and repeated rejections
both terminate with instructions instead of burning the recursion limit.

## 7. Make degradation honest: a deterministic fallback

When synthesis fails or is rejected twice, ship a clearly labeled
deterministic summary assembled from the same fixed SQL — same validators
apply. Label it in-band ("[Deterministic evidence summary — AI synthesis
temporarily unavailable]") and record the source in your audit trail
(`synthesis` / `deterministic_fallback`). Users kept working through a full
LLM outage; nothing was ever passed off as AI output that was not.

## 8. Do not let the model invent geography — or risk explanations

Synthetic GPS confused our model into "the California region (lat 34.9, lon
135.3)" — those coordinates are not California. Fixes that held:

- Prompt rule: report coordinates as recorded; never attach place names
  unless the place name exists in retrieved evidence.
- A deterministic validator check: a sentence containing both explicit
  coordinates and a region/area/zone label is rejected.
- The dataset's own README was the authority for *why* (intentional region
  mixing) — read your data documentation before judging model output.

The same applies to risk classifications: if the data does not store how a
`risk_level` was derived, the agent must say so, and must keep four layers
separate: envelope compliance, recorded risk flags, confirmed incidents, and
the receiver's verdict. "Accepted with no incidents" is not "problem-free."

## 9. SOP retrieval needs its own failure story

Vector-retrieval of procedures will time out eventually. Design the answer
for that day: state unavailability, never substitute an unrelated document,
never invent citations, and keep giving the SQL facts. Validate accordingly
(see §5).

## 10. Acceptance is a browser conversation, not a curl

Gateway-level tests prove plumbing. Acceptance is one continuous browser
conversation: greeting → report → contextual follow-up → history →
incidents → comparison → follow-up → SOP — then a second conversation about a
different shipment to prove isolation. Record per-question latency and the
correlated request ids. If any step is only verified server-side, say so.

## Summary checklist

- [ ] OpenAI-shaped errors with user-actionable messages
- [ ] Per-path bounded timeouts + failure classification
- [ ] Deterministic entity prefetch through the guarded tool
- [ ] Intent-based context resolution, no global state
- [ ] Intent-based validation + honest-unavailability rules
- [ ] One SQL guard, one tool, metered loops
- [ ] Labeled deterministic fallback, audited source
- [ ] No place names / no invented risk causes
- [ ] SOP failure story
- [ ] Browser acceptance with per-question latency
