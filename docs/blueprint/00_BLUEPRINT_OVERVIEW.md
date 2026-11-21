# Blueprint Overview — Cold-Chain Logistics AI-Assistant

Status: ARCHITECTURE REVIEW — REVISION 5 (2026-11-03, final consistency
corrections; major architectural decisions accepted as of Revision 4). No
application code, deployment, or infrastructure provisioning has been
performed or is authorized by this document set. Authoritative
implementation basis: the repository as of 2026-11-03 plus
`GLM_5_3_PROJECT_GUIDELINES.md`.

**Revision 5** (findings R5-1–R5-3 in
[`12_AUDIT_CORRECTIONS.md`](12_AUDIT_CORRECTIONS.md)) corrects the SNS FIFO
guidance (FIFO topics do not support direct email subscriptions — SNS
Standard retained; a future FIFO pipeline would need an SQS FIFO consumer
plus a separate email mechanism), withdraws the overclaim that all duplicate
publications are database-detectable (at-least-once semantics with an
ack→insert crash window: possible duplicate emails and missing attempt
records; topic CloudWatch metrics are the cross-check), and standardizes the
Streamlit gate to `authenticate-cognito` across all documents. Revisions
2–4 (C1–C15, R3-1–R3-10, R4-1–R4-5) remain in effect.

**Revision 6 addendum (2026-11-08, post-approval operator decision R6-1):**
the application's runtime LLM is changed from Ollama Cloud GLM-5.3 to
**NVIDIA NIM** (`deepseek-ai/deepseek-v4.1-flash` at adoption), now the *only*
supported provider — the Ollama/OPENAI/DEEPSEEK/OLLAMA_LOCAL branches were
removed from the code and unsupported values hard-fail. **P-8 amendment
(2026-11-14, ledger R6-2): the production model within NIM is
`z-ai/glm-5.3-flash`** (post-repair qualification 14/15 complete_correct),
replacing DeepSeek v4.1 Flash as the default; the provider decision itself is
unchanged. Full record and consequences in
[`12_AUDIT_CORRECTIONS.md`](12_AUDIT_CORRECTIONS.md) §Revision 6 and the
banner in `02_OLLAMA_CLOUD_RUNTIME.md`. Where these documents say "Ollama
Cloud GLM-5.3" as the primary runtime, read "NVIDIA NIM GLM-5.3 Flash"
subject to R6-1/R6-2; all other architecture decisions stand.

## Purpose

This is the complete architecture blueprint requested after review of
`PROJECT_UNDERSTANDING.txt`. It designs the target state (AWS deployment with
Terraform, Ollama Cloud GLM-5.3 as primary LLM, unified dataset preservation)
while rigorously distinguishing what exists today, what was locally verified
during this review, what remains unverified, and what is only planned.

## Document index

| # | Document | Covers requirement |
|---|----------|--------------------|
| 01 | [AWS_ARCHITECTURE.md](01_AWS_ARCHITECTURE.md) | Req 1 — VS Code → GitHub Actions → AWS, Terraform, EC2 containers, RDS, S3, SQS, SNS, IAM, Secrets Manager, CloudWatch, all apps |
| 02 | [OLLAMA_CLOUD_RUNTIME.md](02_OLLAMA_CLOUD_RUNTIME.md) | Req 2 — GLM-5.3 primary runtime, no GPU, auth, tool calling, usage monitoring, error handling |
| 03 | [DATA_AND_EVENT_PROCESSING.md](03_DATA_AND_EVENT_PROCESSING.md) | Req 3+4 — unified dataset, SQL Server design, Floci→AWS event processing, provenance, idempotency, retries |
| 04 | [DASHBOARD_INTEGRATION.md](04_DASHBOARD_INTEGRATION.md) | Req 5 — every dashboard/agent feature mapped to tables, views, and APIs |
| 05 | [SECURITY_MODEL.md](05_SECURITY_MODEL.md) | Req 6 — least-privilege views for all consumers, gateway auth, secrets, network boundaries, writer permissions |
| 06 | [SOP_RETRIEVAL.md](06_SOP_RETRIEVAL.md) | Req 7 — Pinecone ingestion design without AWS compute, cargo-specific rules, human review |
| 07 | [AWS_COST_ESTIMATE.md](07_AWS_COST_ESTIMATE.md) | Req 8 — monthly cost tiers; AWS vs Ollama charges |
| 08 | [VERIFICATION_MATRIX.md](08_VERIFICATION_MATRIX.md) | Req 9 — per feature: implemented / locally verified / unverified / planned, with file evidence |
| 09 | [IMPLEMENTATION_ROADMAP.md](09_IMPLEMENTATION_ROADMAP.md) | Req 10 — file-level phases, dependencies, acceptance criteria, rollback, test plans |
| 10 | [ARCHITECTURE_DECISIONS.md](10_ARCHITECTURE_DECISIONS.md) | ADRs 1–18, incl. corrected/new decisions: ADR-3 (C3, R3-3), ADR-4 (C5), ADR-5 (C8), ADR-6 (C6), ADR-7 (C9, R3-2), ADR-8 (C11), ADR-10 (C10), ADR-13 (C1), ADR-14 (C2), ADR-15 (C15, R3-5), ADR-16 (C7, R3-1), ADR-17 (R3-6), ADR-18 (R3-8) |
| 11 | [GAP_ANALYSIS_MATRIX.md](11_GAP_ANALYSIS_MATRIX.md) | Consolidated gap analysis across all requirements + audit corrections |
| 12 | [AUDIT_CORRECTIONS.md](12_AUDIT_CORRECTIONS.md) | Audit issue → correction → affected documents → unresolved decisions → official doc URLs |

## Ground rules used throughout (from GLM_5_3_PROJECT_GUIDELINES.md)

1. `data/unified/` is the only canonical dataset (2021–Oct 2026, synthetic).
2. Nothing is claimed operational because its configuration exists. Every
   capability is tagged with verification status and file evidence.
3. The agent never executes DDL/DML; SQL AST allowlists are extended only with
   updated threat-model tests.
4. No silent LLM fallback; no real AWS resources without explicit
   authorization; demo events keep `source=synthetic` forever.
5. SOP outputs are recommendations for qualified human review only.
6. Floci/SQL Server/Grafana/LibreChat/Pinecone/live Ollama integrations are
   NOT verified in this environment and remain so labeled.

## Verification evidence collected during this review

- SQLite dataset live-validated: 4,020 shipments / 196 vehicles / 4,028
  sensors / 64,120 telemetry / 10,030 custody / 8,094 alerts / 64,120 route
  events; `PRAGMA foreign_key_check` clean. Matches
  `data/unified/DATASET_MANIFEST.json` exactly.
- Offline test suite run on this Windows machine: **20 of 21 pass**. The one
  failure is a Windows-only encoding defect in the test itself
  (`tests/test_llm_provider.py:8` uses `read_text()` without
  `encoding="utf-8"` against `src/orchestrator.py`, which contains a non-ASCII
  glyph at line 92); it passes on the GitHub Ubuntu runner. Recorded as a
  Phase 0 gap, not silently glossed over.
- Absence claims verified by code search: no Terraform anywhere in the repo;
  no `AgentAuditLog` implementation; DLQ/outbox appear only as documented
  limitations in `docs/` and `integrations/floci/README.md`.
- `notes.pdf` (5 scanned pages, no text layer, no OCR available) remains
  unread; if it contains requirements, they are not reflected here.

## Not included / out of scope

- Any code change, `terraform apply`, container start, or service
  provisioning (explicitly withheld until blueprint approval).
- Real AWS account, region, or billing validation.
- Live GLM-5.3 tool-calling evidence (requires Ollama credentials).