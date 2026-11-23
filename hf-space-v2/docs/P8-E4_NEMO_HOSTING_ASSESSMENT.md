# P8-E4: NeMo Guardrails Python Hosting Assessment (rev.3, corrected 2026-11-22)

**Architecture correction:** Google Cloud Run and GCP provisioning REMOVED from
the selected plan per operator directive. Objective: **zero recurring infrastructure
charges.** No GCP, AWS, Azure or billing-enabled cloud platform without explicit
operator approval.

## Selected architecture (unchanged)
- Hugging Face Static: public portfolio
- Supabase Free: Google auth, quotas, gateway
- Pinecone Starter: existing SOP retrieval
- NVIDIA NIM: real inference within approved allowance
- **NeMo Guardrails: mandatory Python security service**

## Constraint

NeMo Guardrails (`nemoguardrails==0.25.0`) is a Python framework. Supabase
Edge Functions use Deno/TypeScript and cannot execute the Python package.
The guardrails must run in a separate Python service that the Supabase
gateway calls over HTTPS.

## Primary candidate: Render Free

| Property | Value | Assessment |
|---|---|---|
| Free tier | 750 hours/month (always-on for 1 service) | Sufficient for a portfolio |
| Memory limit | **512 MB** | ⚠️ CRITICAL CONSTRAINT — must measure actual usage |
| CPU | Shared | Sufficient for deterministic rails (~15ms) |
| Cold start | ~50s (after 15 min inactivity) | ⚠️ Unacceptable for user-facing AI; acceptable for a portfolio demo |
| Wall-clock timeout | 100 seconds (free tier) | Sufficient for guardrail execution |
| HTTPS | Automatic (`.onrender.com` subdomain) | Yes |
| Python support | Native (Dockerfile or buildpack) | Yes |
| Custom domain | Free tier: no | `.onrender.com` suffices for service-to-service |

### 512 MB memory concern

The NeMo Guardrails package + FastAPI + uvicorn + pydantic dependencies must
fit within 512 MB of container memory. The **deterministic rails** (regex
patterns, tool allowlist, HMAC auth) are extremely lightweight — the NeMo
framework itself (with its LLM orchestration) is the memory-heavy component.

**Measurement required (Stage 6):** build the Docker image locally and
measure actual RSS with `docker stats` to determine whether:
- (a) The full `nemoguardrails` package fits in 512 MB
- (b) Only the deterministic rails (without NeMo framework imports) fit in 512 MB
- (c) Neither fits — a larger host is required

### Cold start behaviour

Render Free services spin down after ~15 minutes of inactivity. The cold start
(≈50 seconds) means:
- **Portfolio visitor experience:** unacceptable if they're waiting for a live
  AI answer. However, the offline portfolio is fully independent and the live
  AI is already gated behind Google auth + quotas + "currently disabled" status.
- **Mitigation:** a keep-alive is NOT permitted (Supabase inactivity rule was
  already rejected by the operator). The service accepts cold starts as a
  consequence of the free tier.
- **Supabase gateway handling:** the gateway's 150s wall-clock limit covers a
  50s cold start + 15ms guardrail execution with 85s remaining for NVIDIA inference.

## Fallback candidates (if Render Free 512 MB is insufficient)

| Service | Free tier | Memory | Assessment |
|---|---|---|---|
| **Fly.io free** | 3 shared VMs (256MB each) | 256 MB | Likely too small for NeMo |
| **Koyeb free** | 1 service | 512 MB | Same limit as Render; different platform |
| **Hugging Face Spaces (Docker)** | Free CPU tier | 16 GB | Designed for demos, not API services; public URL = not ideal for a security service |
| **Back4App containers** | Free tier | 256 MB | Likely too small |

**If 512 MB is insufficient for the full NeMo framework, the fallback is to
run ONLY the deterministic rails (without importing nemoguardrails) in the
production service.** This is architecturally sound because:
- The deterministic rails ARE the active security gate (100% detection, 0 FP)
- The NeMo framework provides orchestration/routing but the LLM-based
  self-check rails are NOT deployed (they use the mock LLM)
- The production service already uses custom regex patterns for ALL detection
- The NeMo framework is a dependency of the LOCAL PROTOTYPE, not of the
  production rail logic itself

## Service-to-service authentication (unchanged: HMAC Option B)

The NeMo service accepts requests only from the Supabase gateway via
HMAC-SHA256 signed requests with:
- Shared secret in Supabase and Render environment variables (never in code)
- Method + path + body digest + timestamp + nonce signature
- 30-second replay-validity window
- Unique nonce per request (replay prevention)
- Constant-time signature comparison
- Strict body size limits (32 KB)
- No secret disclosure in errors or logs

## Availability and timeout handling

| Scenario | Gateway behavior |
|---|---|
| Render service cold-starting (50s) | 504 timeout (fail-closed, no NVIDIA call) |
| Render service unreachable | 503 (fail-closed, no NVIDIA call) |
| Guardrail execution error | 502 (fail-closed, no NVIDIA call) |
| Auth failure | 401 (request rejected) |
| Service overloaded | 429 (backpressure) |

**In ALL failure cases: NVIDIA inference is NOT called.**

## Decision gate (unchanged)

Deploying the NeMo Python service requires:
1. ✅ Memory measurement confirms 512 MB sufficiency (Stage 6)
2. ✅ Operator approval for Render Free deployment
3. ✅ Operator confirmation of NVIDIA public-demo terms
4. ✅ End-to-end staging tests
5. ✅ Emergency shutdown verified

**Not deployed. Awaiting operator decisions.**
