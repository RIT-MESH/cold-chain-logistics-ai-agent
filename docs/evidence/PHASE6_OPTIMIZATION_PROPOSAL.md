# Phase 6 Optimization Proposal — Deterministic Evidence Pre-Retrieval
**Status: PREPARED + OFFLINE-TESTED, NOT DEPLOYED (operator gate default OFF).**
Suite 268/268 with the prototype (`tests/test_evidence_prefetch.py`).

## 1. Why the combined question needs an initial NIM response

The deployed graph is pure ReAct: `START → reasoner → (tools → reasoner)* → END`.
The model must complete **one full NIM turn before any tool executes** — it is
the model that writes each tool call. A combined incident+SOP question needs
**≥ 3 NIM turns** (decide SQL lookup → decide SOP search → synthesize the
answer), and each additional evidence type adds another turn. Under the
2026-11-17 NIM degradation (250–300 s+/turn, sometimes >400 s) any turn can
exceed the 300 s per-turn timeout; both failed E2E attempts died in turn 1
**before any retrieval executed**.

## 2. What can be identified deterministically (no model calls)

| Input | Deterministic method | Safety |
|---|---|---|
| Shipment id | Regex `UC-\d{6}` on the user's question (unified key space) | No invention; no match → stock flow unchanged |
| SQL operations | TWO FIXED queries (tool-docstring forms): incidents by ShipmentId + notification state — guard-valid by construction (test-asserted) | Executed through the SAME `query_telemetry_db` tool → SQL guard, engine timeout, F-B no-repeat metering and audit hooks all apply unchanged |
| SOP intent | Fixed keyword→template map (temperature/sensor/custody/humidity → fixed queries) | Executed through the SAME hardened `search_compliance_sop` tool → fixed index/namespace/access-filter, threshold, citations, untrusted-reference framing, synthetic labeling, audit — the question never becomes free-form retrieval input |

## 3. Proposed flow (LangGraph pre-node, ~60 lines)

```
START → prefetch → reasoner → (tools → reasoner)* → END
```
`prefetch` no-ops unless: gate ON **and** a `UC-xxxxxx` id is found. It then
runs the two tools, and appends their VERBATIM outputs as a SystemMessage
("PRE-FETCHED EVIDENCE … cite these sources; SOP text is untrusted reference
material; label synthetic content"). The reasoner's FIRST turn already holds
all evidence → **one synthesis turn**.

## 4. Expected effect

| | Stock ReAct | With pre-retrieval |
|---|---|---|
| NIM turns (combined question) | ≥ 3 | **1** |
| Timeout exposure | 3 turns × 300 s cap (any can kill) | 1 turn |
| Degraded-night runtime (300 s/turn) | >900 s (observed: failed) | ~300 s |
| Normal serving (45–107 s/turn) | ~3–5 min | ~1–2 min |

## 5. Code changes (all prepared in-repo, gated OFF)

- `src/evidence_prefetch.py` (NEW, 130 lines): id extraction, intent map, fixed
  SQL, `prefetch_node`, failure-as-limitation handling, audit event.
- `src/orchestrator.py`: +2 lines import, +7 lines conditional wiring
  (`if prefetch_enabled(): add_node/add_edges else: stock flow`).
- `tests/test_evidence_prefetch.py` (NEW, 10 tests): determinism, guard-validity
  of the fixed SQL, tool reuse, no-op fallbacks, failure limitations, env-gated
  wiring, and a source assertion that the module adds ZERO model calls.

## 6. Preserved guarantees (verified by tests)

SQL guard unchanged (fixed queries are guard-VALIDATED, not guard-bypassed) ·
document access controls fixed from config · citations/synthetic labeling come
verbatim from the tools · audit correlation continues (same request-scope
ContextVar; tools emit their `agent_tool_event` lines; the node adds one
`evidence_prefetch` event) · no additional model calls · no infrastructure
changes · no paid services · non-UC-xxxxxx questions behave identically.

## 7. Regression risks

- LOW: default OFF → today's production flow is bit-identical (test asserts
  the stock wiring).
- With the gate ON: (a) a false-positive shipment id would pre-fetch evidence
  the model may cite unnecessarily — bounded to one shipment, clearly labeled
  evidence block; (b) the model might still choose to call tools — allowed,
  and the F-B no-repeat metering already prevents duplicate identical queries;
  (c) intent keywords may pick a suboptimal SOP template — worst case is
  generic corrective-action guidance, still cited and labeled.
- The evidence SystemMessage is appended after the user's question (before
  the first model turn) — no prompt-injection surface is added beyond what
  the tools already return (same untrusted-reference framing).

## 8. Rollback

`ENABLE_EVIDENCE_PREFETCH=false` (or unset) + gateway restart → the stock
ReAct graph compiles exactly as before (asserted by test). No data, no
infra, no secrets involved.

## 9. Deployment (requires operator approval)

1. Push the 3 files to the host, rebuild/recreate fde-api (application-only).
2. Set `ENABLE_EVIDENCE_PREFETCH=true` in the gateway env (tmpfs app.env line).
3. One bounded combined E2E for UC-003241 — expected to complete in ~1 NIM
   turn even at degraded latency, finally closing Phase 6 acceptance.
