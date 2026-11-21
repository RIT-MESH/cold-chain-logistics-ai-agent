# Phase 7 — Measured Performance Report (2026-11-18)

All numbers are measured on the live deployment; no stress tests were run
(operator-restricted). **69.9 s is ONE verified E2E measurement, not a
general response-time claim** — general ReAct questions remain multi-turn
and their latency depends on NVIDIA serving conditions.

## The measured Phase 6 E2E (2026-11-17 08:44–08:45 UTC)

| Stage | Latency | Evidence |
|---|---|---|
| Shipment identification (deterministic) | instant | audit `evidence_prefetch` event |
| SQL incident lookup (`vw_telemetry_incidents`) | **230 ms** | audit event, 1 row |
| SQL notification state (`vw_incident_notifications`) | **120 ms** | audit event, 1 row |
| Pinecone SOP retrieval (embed + query, 3 chunks) | **844 ms** | audit event, cited=true |
| Nemotron-3.5-Lightning synthesis (5,577 tokens total; 3,763 completion) | **~68 s** | derived: request_end 69,565 ms − prefetch ~1.2 s |
| **Total (curl)** | **69.93 s** | matches audit 69.565 s to ~0.4 s |

## Model responsibility (current, deployed)

- **GLM-5.3-Flash (`z-ai/glm-5.3-flash`)** — the PRIMARY agent model: general
  reasoning and the original ReAct tool workflow for every question. Qualified
  14/15 with zero API failures; every historical answer quality record
  (Phase 3/5 E2E passes) is GLM's.
- **Nemotron-3.5-Lightning (`nvidia/nemotron-3.5-lightning-30b-a3b`)** — the
  FINAL EVIDENCE-GROUNDED SYNTHESIS TURN ONLY for prefetch-recognized
  incident+SOP questions (fixed config via `SYTHESIS_MODEL`; no automatic
  switching; GLM remains for everything else).
- **Nemotron-3.5-Embed (`nvidia/nemotron-3.5-embed-1b`)** — SOP document and
  query embeddings (2048-d, input_type passage/query per call).

## The historical GLM latency problem and the adopted resolution

- Recorded normal GLM: 45–107 s/turn, multi-tool medians 78–205 s (Phase 0
  qualification). Degraded windows (2026-11-15 and 2026-11-17): 250–400 s+ per
  trivial turn — the combined incident+SOP question needed ≥3 sequential GLM
  turns (ReAct: decide-SQL → decide-SOP → synthesize) and timed out twice at
  the 1500 s cap, both times BEFORE any tool executed.
- Resolution (operator-approved): (1) deterministic evidence prefetch — the
  shipment id and retrieval operations are identified without any model call,
  collapsing ≥3 turns to 1; (2) Lightning for the single synthesis turn
  (live-probed ~1 s/turn; measured ~68 s for a 3.7k-token grounded answer);
  (3) the deterministic 16-check validator gates the output — Lightning's
  first attempt was correctly REJECTED for an incomplete citation, proving
  the gate, before the citation-format fix closed it at 16/16.
- The two Nemotron historical failure classes are structurally addressed:
  tool-selection errors (recursion deaths) cannot occur (no tools in the
  synthesis path); fabrication/misreading is DETECTED and rejected
  (validator: fabricated magnitudes, invented sections, delivery-confusion,
  missing citations/labels all fail the gate — offline-proven with the
  recorded failure examples).

## Other measured metrics (this session)

- Containers: CPU ≤0.7%, memory ≤36% (largest LibreChat 359 MB/1 GB);
  gateway 142 MB even mid-request; disk 61% (20 GB free).
- Reconcile pass: sub-second (lock + queries + metric emission).
- SQS main queue depth: 0; DLQ: 0 (after the intentional test rejects were
  purged with evidence captured); all 8 alarms OK.
- Phase 4 event plane E2E (2026-11-17): excursion event → incident + acked
  notification observed within seconds of upload.
- RDS: db.t3.small, 20 GB — all guarded queries ≤372 ms in every audit
  record; no slow-query evidence.
- Historical reference: Phase 5 GLM E2E (working ReAct, normal serving) =
  531 s total — the prefetch+Lightning path is ~7.6× faster for the combined
  question and removes the multi-turn timeout exposure.

## Reliability observations

- Worker/reconcile: Up 14+ h continuous; crash-fixes (source mount, RDS CA)
  are permanent image/compose changes, verified by the resume + deploy cycles.
- Gateway restart/recreate verified repeatedly (5+ deploys this session);
  nginx restart procedure (stale-DNS) established and scripted in the runbook.
- SQS retry/DLQ: proven live (invalid payload + bad shipment ref → DLQ with
  FailureReason; redrive maxReceiveCount=5; alarm fired correctly).
- No load testing performed; no claim made about production scale.
