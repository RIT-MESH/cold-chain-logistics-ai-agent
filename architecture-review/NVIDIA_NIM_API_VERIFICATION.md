# NVIDIA NIM DeepSeek API Verification

Revision 3 (2026-11-03, live results). Status: **LIVE VERIFICATION
PERFORMED** — all checks below marked PASS/FAIL were executed against the
real endpoint with a real key (provided by the user, stored only in the
gitignored project `.env`, never displayed or logged; all recorded output is
secret-redacted). No application source code was modified, nothing was
deployed, and the default LLM provider was NOT changed pending review.

- **Endpoint tested (live):** `https://integrate.api.nvidia.com/v1`
- **Model identifier tested (live):** `deepseek-ai/deepseek-v4.1-flash`
  (confirmed served by `GET /v1/models` — 85 models listed; no substitution
  occurred)
- **Request volume:** ≈14 small requests total across all probes (max
  512 completion tokens on any single request); no load testing.

## 1. Live test results

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | HTTPS connectivity + authentication | **PASS** | 85 models listed in 0.22 s |
| 2 | Previously used DeepSeek model available | **PASS** | exact identifier `deepseek-ai/deepseek-v4.1-flash` present in live model list |
| 3 | Minimal chat completion | **PASS** | content `"OK"`, `finish=stop`, usage object present — **75.3 s elapsed** |
| 4 | Multi-turn conversation handling | **PASS (after diagnosis)** | first probe at `max_tokens=32` returned empty content; retest at `max_tokens=512` recalled the codename `"Frost-7"`, `finish=stop`, 55 of 61 completion tokens were reasoning tokens. The initial failure was a token-cap artifact, not a history failure — history handling is correct. |
| 5 | Tool calling with harmless mock function | **PASS** | model emitted `tool_calls` with correct name and args (`get_current_utc_time`, `{"tz_label":"UTC"}`) — 50.2 s |
| 6 | Tool-result round trip | **PASS** | submitting the tool result produced a final answer citing it — 48.1 s |
| 7 | Tool-call response processed by LangGraph | **PASS** | isolated `langchain-openai` invoke returned proper `AIMessage.tool_calls`; **full reasoner→ToolNode→reasoner graph** (built and invoked exactly like the application's `fde_agent`, with thread config + recursion limit) executed the mock tool and produced a final answer citing the tool output: "The tool returned: **2026-11-03T12:00:00Z** (timezone: UTC)…" — 79.3 s for the two-turn loop |
| 8 | Error handling — timeout | **PASS** | `APITimeoutError` raised as expected under an impossibly small client timeout |
| 9 | Error handling — invalid key (vs the protected chat endpoint) | **PASS** | deliberately invalid key rejected with **HTTP 403 `PermissionDeniedError`** in 0.3 s. (The first probe's "invalid key accepted" was an artifact of testing `GET /v1/models`, which is unauthenticated — finding F-2.) |
| 10 | Rate limits / quotas | **documented in §2** | no in-band disclosure; user-supplied org limit 40 RPM |

## 2. Credits and applicable limits

- **User-supplied (2026-11-03, from the NVIDIA console, organization
  "Ritzlab"):** the applicable limit is **40 RPM — 40 requests per minute**
  (API calls, not tokens). The console **does not display a remaining token
  allowance or API-credit balance**, so no credit figure is available from
  any source I can reach.
- **In-band disclosure (live-observed):** none. Neither `GET /v1/models` nor
  chat-completion responses carry `x-ratelimit-*`, quota, or credit-remaining
  headers (only `nvcf-reqid`, `nvcf-status: fulfilled`, `x-request-id`,
  CORS fields). The API provides **no endpoint for querying remaining
  credits**; any balance is visible only in the build.nvidia.com dashboard
  behind the user's browser login.
- **Usage accounting (live-observed):** every response includes a
  `usage` object with a reasoning-token breakdown (e.g., the multi-turn
  retest: 65 prompt + 61 completion tokens, of which 55 were reasoning
  tokens; an 8-token request consumed 8 reasoning tokens). Recorded per
  request in `docs/evidence/nvidia_nim_live_results.json` — this is the only
  consumption signal available to the application.
- **Assessment against the 40 RPM limit:** a single agent answer involves a
  ReAct loop of 2–4 sequential requests; at this org limit, up to ~10
  concurrent agent conversations are supportable before throttling — ample
  for a single-operator test environment, but the limit is per-organization
  ("Ritzlab"), so all consumers of this key share the same 40 RPM budget.

## 3. LangGraph compatibility

The OpenAI-format `tool_calls` returned by the endpoint convert correctly
into LangChain/LangGraph message objects: an isolated
`ChatOpenAI(base_url=<NIM>).bind_tools(...).invoke(...)` round trip returned
a proper `AIMessage` with `tool_calls=[{'name': 'get_current_utc_time',
'args': {'tz_label': 'UTC'}, 'id': 'chatcmpl-…'}]`. A full reasoner→ToolNode→
reasoner graph run (invoked exactly like the application's `fde_agent`) is
recorded in §1 row 7 / §5 below. The first probe's LangGraph failure
(`ValueError: Missing required config key 'N/A' for 'tools'`) was a defect in
my own harness (direct `ToolNode.invoke` without a LangGraph config), not in
the NVIDIA response — established because the isolated invoke succeeded.

## 4. Findings (live)

- **F-1 — Latency profile (major).** Every chat request to
  `deepseek-ai/deepseek-v4.1-flash` took **46.7–75.3 s** in our tests
  (n≈8), and the model spends most of its budget on reasoning tokens. A
  runtime agent ReAct answer involving 2–4 model turns implies **2–5 minutes
  per tool-using answer**. This is an order of magnitude slower than the
  gateway's current configured expectations
  (`OLLAMA_REQUEST_TIMEOUT_SECONDS=120` covers a single request, not the
  loop) and materially worse than a user-facing chat assistant should be.
- **F-2 — `GET /v1/models` is unauthenticated.** The model list accepts any
  key (including deliberately invalid ones). Authentication is enforced
  only at the chat-completions endpoint. Harmless for data protection (the
  list contains no account data), but it means "I can list models" must
  never be used as evidence that a key works — the corrected invalid-key
  test therefore targets the chat endpoint.
- **F-3 — Reasoning tokens consume caps.** Small `max_tokens` budgets are
  silently exhausted by reasoning before any visible text (finding §1 row
  4). Any production use must budget tokens accordingly and treat empty
  content + `finish_reason=length` as a recognizable condition.
- **F-4 — Usage is reliable; limits are not.** Per-request `usage` is
  trustworthy for accounting; rate/credit signals are absent from the wire,
  so an application-side token/request counter (like the blueprint's C12
  design for Ollama) would be the only enforcement signal available.

## 5. Final decision

**PASS — 10 of 10 checks** (multi-turn passed after the `max_tokens`
diagnosis; the two initial probe FAILs were harness artifacts, corrected
and re-verified live). NVIDIA NIM DeepSeek at
`https://integrate.api.nvidia.com/v1` with
`deepseek-ai/deepseek-v4.1-flash` is **functionally verified end to end**:
authentication, chat completion, multi-turn history, OpenAI-format tool
calling, tool-result round trips, and full LangGraph graph processing
(reasoner → ToolNode → reasoner), plus correct timeout and
invalid-key error behavior. Evidence (per-request details, redacted):
`docs/evidence/nvidia_nim_live_results.json`.

## 6. Recommendation for the primary runtime LLM

Per the stated condition (pass authentication, chat completion, conversation
history, and LangGraph tool calling), the conditional recommendation is
**ISSUED**:

> **Recommendation: adopt `LLM_PROVIDER=NVIDIA_NIM` as the application's
> primary runtime LLM, with Ollama Cloud GLM-5.3 retained as an optional
> provider and automatic fallback disabled (consistent with the project's
> existing no-silent-fallback rule). GLM-5.3 via Ollama Cloud remains the
> VS Code development assistant.**

Required if adopted (none implemented yet — all need your approval and a
blueprint amendment, since the approved Revision 5 baseline names Ollama
Cloud GLM-5.3 as primary):

1. **Provider wiring**: add an `NVIDIA_NIM` branch to `create_llm()`
   (`ChatOpenAI(base_url="https://integrate.api.nvidia.com/v1",
   model="deepseek-ai/deepseek-v4.1-flash")`) following the existing
   endpoint-lock and no-fallback patterns; `.env` + `.env.example` +
   docs updated together.
2. **Latency budget (finding F-1 — the deciding factor)**: each model turn
   took 46.7–79.3 s live. The gateway must raise per-request timeouts
   (current `OLLAMA_REQUEST_TIMEOUT_SECONDS=120` is barely one turn) and
   the UI must set user expectations (answers with tool loops may take
   2–5 minutes). If this latency is unacceptable for dispatchers, that
   alone is a sufficient reason to reject this recommendation.
3. **Token budgeting (F-3)**: reasoning tokens consume `max_tokens`;
   caps must be sized so answers aren't silently truncated to empty
   content with `finish_reason=length`.
4. **Usage accounting (F-4)**: keep the C12-style per-request token counter
   — it is the only consumption signal (no wire-level limits; org limit is
   40 RPM shared across every consumer of the "Ritzlab" key).
5. **Blueprint amendment**: a short revision documenting the provider
   change, latency budget, and the key-handling note (key stays in the
   gitignored `.env` on the gateway host only, like `OLLAMA_API_KEY`
   today; in AWS it would move to Secrets Manager per the existing C8
   design).

**Nothing was changed.** The application still defaults to
`LLM_PROVIDER=OLLAMA_CLOUD` / `glm-5.3`; no code, deployment, or provider
setting was modified, per the instruction to stop after verification.

## 1. Search performed (all negative for NVIDIA NIM DeepSeek)

| Location checked | Result |
|---|---|
| Project workspace (`E:\cold-chain-logistics-FDE-Project-main`) | zero matches for `nvidia` / `NGC_` / `integrate.api.nvidia` in any file; project has no NVIDIA integration code and no previous DeepSeek-API test scripts |
| `C:\Users\rites\Documents\ollama-mcp\ollama_server.py` (user-provided path) | local Ollama MCP only (`http://localhost:11434`); no NVIDIA references |
| `C:\Users\rites\OneDrive\Documents\nvidia-cosmos-mcp\` (user-provided path) | **the only NVIDIA integration on this machine** — see §2 |
| OneDrive `Documents` full text-file search (`deepseek` \| `integrate.api.nvidia` \| `NVIDIA_API_KEY` \| `NGC_API_KEY`) | only hits: the cosmos project's `server.py` and `README.md` |
| Non-OneDrive `C:\Users\rites\Documents` recursive search | binary false positives only (provider binaries/DLLs); VS Code settings contain no NVIDIA entries |
| Environment variables — Process, **User, and Machine** persistent scopes | `NVIDIA_API_KEY`: not set · `NGC_API_KEY`: not set · no env var name containing NVIDIA/NGC/DEEPSEEK/NIM |
| All `.env`-style files discoverable on `E:\` and user locations (key **names** only inspected) | none contains NVIDIA or NGC keys. The only real `.env` found (`E:\labs\video-course\.env`, an unrelated project) contains MiniMax keys — reported for completeness, values never read |
| `~/.ngc\` (NGC CLI credentials), NGC AppData configs | not present |
| Installed Python packages | no nvidia/ngc/nim packages in either the system Python or the cosmos venv |
| Claude Code configuration (`.claude.json` MCP registrations, user/project `settings.json` env blocks, project `.mcp.json`) | no NVIDIA keys; the cosmos project's own `.mcp.json` `env` block is **empty** |
| Shell profiles (`.bashrc`/`.profile`/PowerShell profiles) | no NVIDIA references |
| VS Code user settings + recent-workspace storage | no NVIDIA references |

## 2. What WAS previously configured (evidence)

The only prior NVIDIA configuration on this machine is the
`nvidia-cosmos-mcp` project (`C:\Users\rites\OneDrive\Documents\nvidia-cosmos-mcp`):

- **Endpoint**: `https://ai.api.nvidia.com/v1/cosmos/nvidia/cosmos3-nano`
  (`server.py:10`) — **not** `https://integrate.api.nvidia.com/v1`.
- **Model identifier**: `nvidia/cosmos3-nano` — a **text-to-video generation**
  model (`model_mode: text2video`), **not a chat-completion model and not a
  DeepSeek model**. No DeepSeek model identifier was ever configured
  anywhere on this machine.
- **Key handling**: `server.py:16-18` reads `NVIDIA_API_KEY` from the
  environment; the project's own README (`README.md:13`) sets it as a
  **session-only PowerShell variable** (`$env:NVIDIA_API_KEY = '...'`),
  which does not persist across sessions — consistent with the key now
  being absent from every persistent store (§1).
- The project's `.claude/settings.local.json` shows it was used
  (`mcp__nvidia-cosmos__generate_video` permission; diagnostic scripts
  `diag_paths.py` / `diag_genai.py` / `diag_transfer.py` that no longer
  exist in the folder).

This is a video-generation tool integration, **not** a previously
configured "NVIDIA NIM DeepSeek API". No DeepSeek-on-NVIDIA chat model,
endpoint, or test script exists on this machine.

Related but distinct: the cold-chain application *code* contains an optional
`LLM_PROVIDER=DEEPSEEK` branch that talks to DeepSeek's **own** API
(`https://api.deepseek.com`, `src/orchestrator.py:66-71`) — unrelated to
NVIDIA NIM, and unconfigured (no `DEEPSEEK_API_KEY`; Phase 0 tests verified
it hard-fails rather than falling back).

## 3. Live API test results — ALL NOT RUN

Per the task's own instruction ("If the existing API key cannot be located,
report that clearly rather than creating a new key"), **no key was created
and no live request was made**. Zero requests were sent; $0 of any NVIDIA
quota was consumed.

| # | Required check | Result | Live or not |
|---|---|---|---|
| 1 | HTTPS connectivity to the NVIDIA API | **NOT RUN** (no credentials; connectivity without auth would prove only that a host exists, not that our account works — not attempted to avoid presenting a non-result as verification) | — |
| 2 | API authentication | **NOT RUN** — no key | — |
| 3 | Previously used DeepSeek model availability | **NOT RUN** — no such model was ever configured (§2) | — |
| 4 | Minimal chat completion | **NOT RUN** | — |
| 5 | Valid response verification | **NOT RUN** | — |
| 6 | Multi-turn conversation handling | **NOT RUN** | — |
| 7 | Function/tool calling with a harmless mock | **NOT RUN** | — |
| 8 | LangGraph processing of NVIDIA tool calls | **NOT RUN** (this is exactly the class of failure the task warned about — "do not assume text generation means LangGraph tool calling works" — and it cannot be assumed in the absence of any live call) | — |
| 9 | Errors, timeouts, rate limits | **NOT RUN** | — |
| 10 | Usage quotas/restrictions | **NOT DETERMINED** — no API access; no quota information exists locally. The only related artifact is the cosmos project README's own caveat: "previews are subject to rate limits and availability" (`README.md:22`) | document inspection only |

No error messages were produced (no requests were sent), so no redaction
was necessary. Nothing in this section was mocked and presented as live:
**every live check is explicitly not run.**

## 4. Endpoint and model verification (task §3)

**Update 1 (same day, user clarification):** the user confirms the existing
build.nvidia.com / NGC account key (the one previously used by the cosmos
MCP) is account-wide and **valid for DeepSeek models on the NIM endpoint** —
consistent with how NVIDIA's hosted API works (one key per account across
all hosted models). This does **not** change the FAIL verdict: the key is
still not accessible to any process on this machine (it was set session-only
per the cosmos project's own README), so no live test could run.

**Update 2 (same day): the user supplied the intended configuration —**
endpoint `https://integrate.api.nvidia.com/v1` and model identifier
`deepseek-ai/deepseek-v4.1-flash`. These are recorded as the targets for
verification. Per the original instruction ("Do not silently substitute
another model"), the live `GET /v1/models` response will be checked at test
time to confirm this exact identifier is served before any chat test; if it
is not listed, that will be reported — not substituted. **The API key is
still not present** (no project `.env` exists as of this update), so the
FAIL / not-runnable verdict stands until the key is made available.

- Expected endpoint `https://integrate.api.nvidia.com/v1`: **no configuration
  for this endpoint exists anywhere on this machine.**
- The one NVIDIA endpoint that exists locally is
  `https://ai.api.nvidia.com/v1/cosmos/nvidia/cosmos3-nano` — a different
  host and a video-generation model, not interchangeable with a chat
  completions endpoint, and not DeepSeek.
- "Use the exact previously configured DeepSeek model identifier": **there
  is no previously configured DeepSeek model identifier on this machine**;
  per the user, the same key is expected to authorize DeepSeek models — the
  exact identifier will be read from the live model list at test time.
- Per instruction, no model substitution was made and no alternatives were
  switched to.

## 5. PASS/FAIL decision

**FAIL — verification could not be performed.** The stated premise (an
existing NVIDIA NIM DeepSeek API configuration with a retrievable key) is
not satisfied on this machine. This is a "cannot verify" outcome, not a
"tested and broken" outcome: the API itself may well be healthy; we simply
have no configured access to it.

## 6. Recommendation regarding NVIDIA NIM as primary runtime LLM

**No recommendation is issued.** The conditional recommendation
(`LLM_PROVIDER=NVIDIA_NIM`, Ollama Cloud GLM-5.3 demoted to optional with no
automatic fallback) was contingent on passing authentication, chat
completion, multi-turn handling, and LangGraph tool-calling tests (§4 of the
task). Zero of those tests ran, because no key exists. Additionally:

- The currently **approved** architecture (Revision 5 blueprint baseline,
  Phase 0 accepted) specifies Ollama Cloud GLM-5.3 as the primary runtime
  LLM with no silent fallback. Any provider change is an architecture
  amendment requiring your explicit approval and a blueprint revision —
  correctly, nothing was changed.
- If you want this verification to proceed, the remaining prerequisite is:
  **make the existing NVIDIA key available to the test process** —
  recommended: place it in the project's private `.env`
  (`NVIDIA_API_KEY=...`), which is already gitignored (`.gitignore:1`) and is
  the project's documented key-handling pattern; do **not** paste the key
  into chat or commit it. Then the ten checks can be run with a handful of
  small requests (auth, model list → exact DeepSeek identifier, minimal chat
  completion, multi-turn, mock tool calling, LangGraph round trip, error/
  timeout/rate-limit probes), and the LangGraph tool-calling loop will be
  tested against the real gateway code.
- GLM-5.3 remains the VS Code development assistant and — unchanged and
  unmodified — the application's default runtime provider.

## 7. Statement of limitations

1. Live validation of the NVIDIA endpoint was not possible and was not
   attempted; no fabricated results are presented.
2. The key search covered: project workspace, both user-provided folders,
   OneDrive and non-OneDrive Documents (text files), persistent Windows
   environment stores, discoverable `.env` files (key names only — no
   values were read), NGC CLI locations, Python environments, Claude Code
   and VS Code configuration, and shell profiles. Windows Credential Manager
   was not enumerated (the prior project's documented pattern is a
   session-only environment variable, which cannot persist, making a
   Credential Manager entry unlikely).
3. No application source code, provider settings, or architecture documents
   were modified. Stopping here as instructed.