# 02 — Ollama Cloud GLM-5.3 Runtime Design

> **🆕 Revision 6 banner (2026-11-08, operator decision R6-1): the runtime LLM
> of this design is changed to NVIDIA NIM** (`https://integrate.api.nvidia.com/v1`)
> — the only supported provider; the Ollama Cloud path was removed from the
> code. **P-8 amendment (2026-11-14, ledger R6-2): the production model within
> NIM is `z-ai/glm-5.3-flash`** (post-repair qualification 14/15 complete_correct
> — `docs/evidence/NVIDIA_NIM_POST_REPAIR_QUALIFICATION.md`), replacing
> `deepseek-ai/deepseek-v4.1-flash`. Live NIM verification
> 2026-11-03: 10/10 PASS (`architecture-review/NVIDIA_NIM_API_VERIFICATION.md`).
> Consequences carried into the design: per-request timeout 180 s
> (`NVIDIA_REQUEST_TIMEOUT_SECONDS`) because one NIM turn is live-measured at
> 46.7–79.3 s and a ReAct answer needs 2–4 turns; output cap 4096 because
> reasoning tokens consume `max_tokens` (F-3); token accounting (C12) is
> implementable immediately — NIM returns a `usage` object with
> reasoning-token breakdown on every response (resolves U-6, metric renamed
> `ColdChain/NimTokens`); rate-limit ceiling is 40 requests/minute org-wide
> (replaces the Ollama Max 10-concurrent figure). Everything else in this
> document (no GPU, endpoint lock, no fallback, secrets handling, Phase 0
> live gate C13) carries over unchanged — C13 is re-targeted from Ollama to
> NIM and its API-level portion is already satisfied by the 10/10 evidence;
> the remaining piece is one live conversation through the running gateway.
> Full ledger entry: `12_AUDIT_CORRECTIONS.md` §Revision 6.

Revision 2 (2026-11-03, post-audit: added usage tracking/spending safeguards
C12; live verification moved earlier C13). Status: current implementation
documented + AWS target design. **The live behavior of GLM-5.3 over Ollama
Cloud is NOT verified** — the provider tests mock the client
(`tests/test_llm_provider.py`), `docs/OLLAMA_CLOUD_GLM_5_3.md` §Verification
states this explicitly, and no credentials were available in this review.
The early live gate that resolves this is defined in Phase 0 of the roadmap.

## 1. Current implementation (evidence)

| Concern | Implementation | Evidence |
|---|---|---|
| Provider default | `LLM_PROVIDER=OLLAMA_CLOUD`; cloud first, no fallback | `src/orchestrator.py:38-42` |
| Endpoint lock | `OLLAMA_CLOUD_BASE_URL` must be exactly `https://ollama.com/v1` or `ValueError` | `src/orchestrator.py:43-46` |
| Model | `OLLAMA_CLOUD_MODEL=glm-5.3` default | `src/orchestrator.py:47` |
| Client | `langchain_openai.ChatOpenAI`, temperature 0, timeout 120s, retries 2, max_tokens 2048 | `src/orchestrator.py:53-61` |
| Key handling | Server-side only; `RuntimeError` if missing; never in YAML/browser | `src/orchestrator.py:40-42`; `tests/test_llm_provider.py:17-18` |
| Optional providers | OPENAI / DEEPSEEK / OLLAMA_LOCAL explicit opt-in only | `src/orchestrator.py:62-77` |
| Tool binding | 3 tools bound via `bind_tools`; reasoner→tools LangGraph loop | `src/orchestrator.py:82-99` |
| Local model | exists, never auto-selected | `src/orchestrator.py:72-76` |

## 2. Primary-runtime design for AWS (no GPU, ever)

```mermaid
sequenceDiagram
    participant U as Dispatcher (LibreChat UI)
    participant LC as LibreChat (EC2 container, compose network)
    participant GW as FastAPI gateway (EC2, NOT publicly routed)
    participant O as Ollama Cloud GLM-5.3 (SaaS — no GPU in AWS)
    participant T as LangGraph tools (SQL/SOP/Weather)
    U->>LC: prompt
    LC->>GW: POST /v1/chat/completions (Bearer FDE_API_KEY, local network)
    GW->>GW: drop caller system/developer/tool roles; bounds (60 msgs / 60k chars)
    loop ReAct (recursion_limit=20)
        GW->>O: chat/completions + tool schema (Bearer OLLAMA_API_KEY from Secrets Manager tmpfs)
        O-->>GW: assistant message or tool_calls (+ usage tokens)
        GW->>T: execute tool (guarded SQL / Pinecone / Open-Meteo)
        T-->>GW: evidence text
    end
    GW->>GW: NEW — aggregate usage_metadata into request-scoped log line (C12)
    GW-->>LC: final answer (single-chunk SSE — NOT token streaming)
```

Requirements mapping:

- **GLM-5.3 primary, local optional** — unchanged; AWS only changes where
  `OLLAMA_API_KEY` lives (Secrets Manager → instance-profile tmpfs fetch,
  `01_AWS_ARCHITECTURE.md` §5).
- **No GPU infrastructure** — the app host is a general-purpose instance;
  nothing in `infra/` requests accelerators; `OLLAMA_LOCAL` stays a
  developer-laptop option outside the AWS bill.
- **API authentication** — two distinct secrets preserved: `FDE_API_KEY`
  (LibreChat→gateway, constant-time compare `api/main.py:40-46`) and
  `OLLAMA_API_KEY` (gateway→cloud).
- **Tool calling** — relies on Ollama's OpenAI-compatible subset
  (`docs/OLLAMA_CLOUD_GLM_5_3.md` warns it is a subset). Resolution moved to
  **Phase 0 live gate** (correction C13): a live, authenticated
  conversation that must complete a real `tool_calls` round trip — using
  `fetch_corridor_conditions` (Open-Meteo), which needs only internet
  access, no SQL Server/Pinecone. Live SQL tool-calling is re-verified at
  Phase 3 acceptance once RDS exists. The application is NOT declared
  functional before these gates pass.

## 3. Usage tracking & spending safeguards (correction C12)

Primary provider remains the Max subscription — nothing here changes
inference routing; it adds visibility and caps.

| Safeguard | Mechanism | Status |
|---|---|---|
| Per-request token accounting | LangChain `AIMessage.usage_metadata` (input/output tokens, returned by OpenAI-compatible APIs) summed over the graph result in `get_answer`; gateway logs one structured line per request: `{session, model, input_tokens, output_tokens, tool_calls}` | planned (Phase 7; requires the C13 live gate to confirm Ollama returns usage fields) |
| CloudWatch metrics | Metric `ColdChain/OllamaTokens` with dimensions direction=in/out → daily SUM + metric-math alarm vs `OLLAMA_DAILY_TOKEN_BUDGET` env default | planned (Phase 7) |
| Concurrency cap | Max plan allows 10 concurrent requests; gateway adds a small in-process semaphore (`OLLAMA_MAX_CONCURRENCY`, default 8) returning 503 when exceeded — protects credits and latency | planned (Phase 7) |
| Per-request hard caps | Already implemented: 60 msgs / 60k chars (`api/main.py:34,97`), recursion 20 (`:81`), `OLLAMA_MAX_OUTPUT_TOKENS=2048`, retries 2 | ✅ |
| Health/quota probe | `scripts/check_ollama_cloud.py` small billed request; wired as a weekly scheduled check after Phase 0 gate | implemented script; schedule planned |
| Billing source of truth | Ollama's account/billing dashboard (subscription, credits, concurrent usage) — our metrics estimate application consumption only; documented procedure in the runbook | process |
| On quota failure | Hard error to the user (502), alarm fires; **never** local fallback (asserted by `tests/test_llm_provider.py:8-16`) | ✅ policy |

Cost basis (from `docs/OLLAMA_CLOUD_GLM_5_3.md`, listed prices — re-validate
before decisions): Max plan $100/mo incl. $300 credits, 10 concurrent
requests; GLM-5.3 $1.40/1M input, $0.26/1M cached input, $4.40/1M output;
≈$0.0086 per 3k-in/1k-out request. **Ollama charges are never part of the
AWS bill** — see `07_AWS_COST_ESTIMATE.md` §1.

## 4. Error handling matrix (current behavior → AWS target)

| Failure | Current behavior (evidence) | AWS target |
|---|---|---|
| Missing OLLAMA_API_KEY | `RuntimeError` at LLM construction → 502 from gateway | Same; Phase 0 gate surfaces it before anything else is built |
| HTTP 429 / quota exhausted | `ChatOpenAI` retry (2) then exception → 502 "Agent execution failed" | Same + CW alarm on 502 rate + token-budget alarm (§3); **never** local fallback |
| Tool errors | Tools return error strings, not exceptions (`agent_tools.py:104,120,163`) — agent must explain, not invent | Unchanged |
| Cloud outage | Gateway 502; Streamlit/Grafana dashboards remain functional (decoupled) | Unchanged; ALB health checks `/health` only |
| Malicious history | system/developer/tool roles dropped server-side | Unchanged (`api/main.py:59-72`) |

## 5. Explicit non-goals

- No local Ollama daemon on EC2 (no GPU, no inference infra; `OLLAMA_LOCAL`
  stays a developer option).
- No model substitution on failure (guidelines §6).
- No reasoning-effort parameters until verified against the live model
  (comment at `src/orchestrator.py:50-52`).