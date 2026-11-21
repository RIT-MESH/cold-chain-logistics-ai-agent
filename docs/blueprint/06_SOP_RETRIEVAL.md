# 06 — SOP Retrieval Design (requirement 7)

Revision 2 (2026-11-03, post-audit C11: documented embedding resource
requirements and a configurable, cost-conscious ingestion workflow).

## 1. Current implementation (evidence)

- One SOP document: `data/policy/Cold_Chain_Incident_SOP_v2.md` (v2.4, eff.
  Jan 2021) — fresh perishables 0.0–4.0 °C, >4.0 °C breach mitigation, port
  congestion >7.0 diversion, High-Risk + delay>0.65 escalation.
- Ingestion: `scripts/ingest_sop_pinecone.py` — polymorphic parser
  (MD/TXT/PDF/CSV/XLSX, lines 97-156), chunk 600/overlap 60, MD5-incremental
  upserts with delete-of-removed files (lines 173-225), batch 100,
  deterministic IDs `{file}-chunk-{n}` (line 213), dimension self-heal
  (purges mismatched index, lines 75-80), hash cache
  `data/cache/ingestion_hash_cache.json`.
- Retrieval: lazy `get_sop_retriever()` (`src/agent_tools.py:53-70`) —
  LOCAL (BGE-M3, 1024-dim, CPU) or OPENAI (1536-dim) indexes kept separate;
  hard `RuntimeError` when unconfigured so chat/SQL remain available.
- Tool: `search_compliance_sop`, k=2, cites source_file+format
  (`src/agent_tools.py:148-164`); prompt requires SOP citations and
  cargo-specific applicability (`src/prompts/system_prompt.txt:6`).

## 2. Embedding resource requirements (measured facts, not guesses)

| Path | Weights | Runtime deps | CPU RAM (observed/typical) | Cold start | Billing |
|---|---|---|---|---|---|
| LOCAL — `BAAI/bge-m3` | ≈ 2.3 GB model files | `torch`, `sentence-transformers`, `langchain-huggingface` (all in `requirements.txt:13-14,19-20`) | several GB for the torch runtime + model (CPU) | minutes (first download) | free (machine-local) |
| OPENAI — `OpenAIEmbeddings` | none | `langchain-openai` + API | trivial | seconds | separate OpenAI bill — **NOT included in the NVIDIA NIM bill** (NIM serves chat completions only; R6-1 — formerly "not covered by Ollama Max") (`src/agent_tools.py:60-61`, `docs/ARCHITECTURE_AUDIT.md:10`) |

Consequence: LOCAL embeddings must NOT run on the always-on AWS app host —
the model + torch runtime would bloat the image and instance RAM for a job
that runs only when an SOP file changes. OPENAI path costs money per chunk
but no compute footprint.

## 3. Configurable ingestion workflow (correction C11)

```mermaid
flowchart LR
    subgraph Trigger["Trigger: SOP file changed (manual, on purpose)"]
        M["Operator edits data/policy/*"]
    end
    M --> CHOICE{"SOP_EMBEDDING_PROVIDER"}
    CHOICE -->|LOCAL (default)| A["dev laptop: python -m scripts.ingest_sop_pinecone<br/>(BGE-M3 CPU, knobs below)"]
    CHOICE -->|LOCAL-EC2 optional| B["run-to-completion EC2 spot job<br/>(small instance, minutes, self-terminates)<br/>— laptop-less operation"]
    CHOICE -->|OPENAI| C["API-based ingestion<br/>(separate OpenAI bill, no model weights)"]
    A --> PC["Pinecone SaaS index<br/>fde-sop-index-local (1024d) /<br/>fde-sop-index-openai (1536d)"]
    B --> PC
    C --> PC
```

- **Provider knob**: `SOP_EMBEDDING_PROVIDER=OPENAI|LOCAL` (maps to the
  existing `Embeddings_model` env read at `agent_tools.py:58`; normalized
  into a single documented setting in Phase 6 — implementation-time change,
  planned). Default stays LOCAL on the developer laptop (ADR-8).
- **Cost knobs**: `SOP_CHUNK_SIZE` (600), `SOP_CHUNK_OVERLAP` (60),
  `SOP_TOP_K` (2), `SOP_UPSERT_BATCH` (100) — all currently defaults
  hard-coded in the script; made configurable in Phase 6 so re-ingestion is
  bounded and predictable.
- **No standing AWS compute** in any path. The optional EC2 spot job is
  run-to-completion with instance self-termination (cents per run) and is
  the only AWS-compute option — off by default.
- **Alignment rules kept** (guidelines §5.6): embedding model ↔ dimension ↔
  index name must match; the script's dimension self-heal
  (`ingest_sop_pinecone.py:75-80`) stays.
- **Incremental by design**: MD5 hash cache skips unchanged files and purges
  deleted ones (lines 173-225) — repeated runs are cheap and idempotent;
  deterministic chunk IDs mean re-ingestion overwrites rather than
  duplicates.

## 4. Cargo-specific safety rules & human review

Current state: SOP limits are explicitly NOT universal —
`GLM_5_3_PROJECT_GUIDELINES.md` §3.5 forbids treating 0.0–4.0 °C as
universal; per-cargo demonstration thresholds live in
`shipments.temperature_min_c/max_c` (`data/unified/README.md:20`); the
worker's 4.0 °C threshold is a demo example (`events.py:32`).

Design additions (planned, Phase 6):

1. **SOP metadata enrichment** at ingestion: `sop_version`,
   `effective_date`, `applicability` chunk metadata from the document
   header, so retrieval can cite version — required by guidelines §5.6.
   Today only `source_file`/`file_format` are stamped
   (`ingest_sop_pinecone.py:210-212`).
2. **Agent contract unchanged**: recommendations carry SOP version +
   applicability and state "human review required" — the incident
   classifier already embeds
   `recommendation: "Human review required…"` (`events.py:38-39`). No
   auto-contact-driver, redirect, or approval actions (guidelines §3.4 —
   currently true and preserved).
3. **Version pinning**: re-ingesting an edited SOP deletes that file's old
   chunks before upsert (`ingest_sop_pinecone.py:199-200`) — exactly one
   live version per file; keep this invariant.

## 5. Verification status

- Ingestion script: 🔶 implemented, never run in this review (no
  PINECONE_API_KEY). The hash cache proves a prior run somewhere, not
  current index state.
- Retrieval tool: 🔶 lazy-init error path verified by reading; live
  Pinecone round-trip unverified (Phase 6 acceptance).
- Prompt behavior (citations, human-review framing): ✅ in
  `src/prompts/system_prompt.txt`; live-model adherence gated by the
  Phase 0 live-LLM gate + Phase 6 transcript evidence.
- Resource table figures: standard published model/deps sizes; re-measured
  at Phase 6 execution before the first real ingest.