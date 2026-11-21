# LibreChat integration (Option A)

This adds an OpenAI-compatible FastAPI gateway around the existing LangGraph agent. The Streamlit shell embeds LibreChat; the former legacy chat UI was retired.

## 1. Gateway

From the repository root, install the existing project dependencies, plus:

```bash
pip install -r requirements-api.txt
```

Configure your existing `.env` for the chosen `LLM_PROVIDER` (default `NVIDIA_NIM` — the only supported provider, R6-1), database, SOP store, etc. Set `FDE_API_KEY` to a strong random secret (e.g., generated with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`).

Start:

```bash
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

For Docker networking, bind to `0.0.0.0` inside a **private** container network; do not publish the gateway directly to the internet. Ensure LibreChat can resolve the gateway host. Health: `GET /health`. Model list: `GET /v1/models` (requires bearer key).

## 2. LibreChat

Copy `integrations/librechat/librechat.yaml` into the LibreChat configuration location and configure LibreChat to load it (typically a Compose bind mount to `/app/librechat.yaml`). Provide `FDE_API_KEY` and `FDE_GATEWAY_URL` as environment variables **in the LibreChat server**. Set the URL to the gateway's network-resolvable base URL ending in `/v1`, such as `http://fde-api:8000/v1` when both containers share a Docker network. Restart LibreChat, choose **Cold Chain AI** and model **cold-chain-agent**.

Refer to LibreChat's current custom endpoint documentation: https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/custom_endpoint

## 3. Verify

```bash
curl http://127.0.0.1:8000/health
curl -H "Authorization: Bearer $FDE_API_KEY" http://127.0.0.1:8000/v1/models
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $FDE_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"cold-chain-agent","messages":[{"role":"user","content":"What can you help me investigate?"}]}'
```

## Limitations / next steps

- Agent executes synchronously. `stream: true` returns a valid SSE completion *after* execution, not incremental tokens.
- Chat context is re-sent by LibreChat on every request; gateway thread IDs are unique per request, so state is not persisted on the backend. Do not send sensitive tool output back as prior chat history without considering authorization.
- LibreChat authentication is not equivalent to backend per-user authorization. Gateway uses one server-to-server API key; add user identity validation and row-level permissions before multi-tenant or public deployment.
- Uploaded documents / images / tool-call transcripts are not passed through by this text-only adapter.
- Runtime correctness requires your SQL Server, Pinecone index, embeddings and selected LLM to be available.
- No deployment or live integration was performed here; test with your actual environment first.

## NVIDIA NIM runtime
The LibreChat custom endpoint remains `cold-chain-agent`. Only the **FastAPI/LangGraph server** connects to NVIDIA NIM (`z-ai/glm-5.3-flash`, the only supported provider since 2026-11-08; glm-5.3-flash adopted as the production model 2026-11-14, blueprint P-8). Configure `NVIDIA_API_KEY` in the gateway environment; never add it to the browser, LibreChat YAML, or Git. See `docs/NVIDIA_NIM_RUNTIME.md`. Note: qualification-measured glm latencies run ~10 s for a simple lookup and 78–205 s median for multi-tool answers — the long wait in LibreChat is expected behavior, not a fault.
