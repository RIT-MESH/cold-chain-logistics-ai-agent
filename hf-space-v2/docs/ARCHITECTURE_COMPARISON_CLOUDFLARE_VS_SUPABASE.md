# Architecture Comparison: Cloudflare vs Supabase (rev.2, corrected 2026-11-22 per operator)

**Corrections to rev.1 (factual):**
1. Cloudflare **SQLite-backed Durable Objects ARE available on Workers Free** — rev.1's
   "paid feature" claim was wrong; free-tier DO quota limits apply but the atomic-quota
   design is feasible at $0 on Cloudflare too.
2. Supabase Free **Edge Functions: 150-second wall-clock and ~2-second CPU-time
   limits** — NOT 400s. Our 90s bounded inference timeout still fits wall-clock, and
   the guardrail/inference work is I/O-bound (CPU-time is mostly idle-waiting), but the
   2s CPU budget is the binding constraint and must be validated in staging.
3. **Supabase free projects pause after ~1 week of inactivity** — per operator
   decision, **no artificial keep-alive**; the deployment simply accepts cold-start
   latency after inactivity, and time-windowed quota counters are unaffected.
4. **NeMo Python service requirement is IDENTICAL under both** — neither Workers nor
   Edge Functions executes Python; a separately deployed Python runtime (directive
   6A.1 Option A/B) is required either way and is its own approval line.

**Reassessed verdict:** Supabase Free remains the **selected** implementation target
(operator decision; confirmed viable): $0 auth+DB+functions, native Google OAuth,
Postgres atomic quotas, and 150s wall-clock accommodates the 90s bounded design —
with two new documented risks: the 2s CPU-time limit (validate: rails + JSON handling
must stay lean) and inactivity pausing (documented mitigation: accept cold-start
latency; no artificial keep-alive). Cloudflare Free is a viable fallback (DOs free), kept in the guide.

Requirements: free-tier eligibility · persistent server-side quotas · Google
authentication · NeMo Guardrails compatibility · inference timeouts · actual
monthly costs · existing Pinecone SOP index reuse (unchanged) · modular
provider-agnostic gateway.

| Requirement | Cloudflare (earlier guide) | Supabase Free (preferred) | Verdict |
|---|---|---|---|
| **Free tier eligibility** | Workers Free **includes** SQLite-backed Durable Objects (limited daily requests) — the atomic-quota design is feasible at $0 | **Free tier includes**: Google OAuth, Postgres DB, 500K Edge Function invocations/mo, row-level security, 50k MAU auth | **Tie** — both feasible at $0; operator selects Supabase |
| **Persistent quotas (atomic)** | Durable Objects (SQLite-backed) — atomic and **available on Workers Free** (limited daily requests) | Postgres with transactions / `pg_advisory_lock` or atomic `UPDATE ... WHERE quota_left > 0` — atomic, persistent, free | **Tie** — both atomic at $0; operator selects Supabase |
| **Google authentication** | Cloudflare Access + Google IdP (Zero Trust org; session assertions validated in Worker) | Supabase Auth `signInWithOAuth({provider: 'google'})` — auto-signup, JWT, RLS-ready, openid/email scopes only | **Supabase wins** (native, one less service + plan) |
| **NeMo Guardrails compatibility** | Worker = Deno/TS — **cannot run Python NeMo**; needs a separate hosted Python service (new paid component) | Edge Functions = Deno — **also cannot run Python NeMo**; same constraint | **Tie** — both need the Python service decision (directive 6A.1 Option A/B/C). Design stays modular: the guardrail runtime is a separate component either way |
| **Inference timeouts** | Workers: CPU-time limits; wall-clock fetch adequate for our 90s bounded waits on free tier | Edge Functions: **150s wall-clock / ~2s CPU-time** — 90s bounded timeout fits; CPU budget is the binding constraint (I/O-bound design expected OK; validate in staging) | **Roughly equal** — both fit the 90s design with caveats |
| **Actual monthly cost** | **US$0** on Workers Free (DOs included; limited daily requests) — the earlier guide's ~US$5/mo Workers Paid plan was for custom domains/Access extras, not required for the core quota design | **US$0** (free tier; our quotas cap usage at ≤100 inferences/day — far below paid thresholds) | **Tie at $0**; Supabase selected |
| **Existing Pinecone SOP index** | Reused via the server-side retrieval contract (fixed index/namespace, injection screening) — unchanged | **Explicitly included** via the same retrieval contract (see below) — unchanged | Included for both; index never modified in prototyping |

## Decision (operator-preferred, confirmed)

**Supabase Free + Google OAuth + Pinecone Starter (existing index) + NVIDIA NIM.**
The gateway is implemented **provider-modular** so nothing is hard-wired:
- `auth/` — `AuthProvider` interface (implementations: `MockAuthProvider` now,
  `SupabaseAuthProvider` at staging)
- `quota/` — `QuotaStore` interface (implementations: `InMemoryQuotaStore` now,
  `PostgresQuotaStore` at staging)
- `inference/` — `InferenceProvider` interface (implementations: `MockNvidiaProvider`
  now, `NimProvider` only after a deployed+verified guardrail runtime)
- `guardrails/` — `GuardrailRuntime` interface (implementations: local Python
  NeMo service for testing; hosted runtime is a separate future approval)

## Pinecone SOP retrieval — explicit inclusion

The architecture includes the **existing** Pinecone SOP index exactly as-is
(prototyping must not modify it):
- Index: `coldchain-sop-index-nim` (2048-d, cosine, us-east-1), namespace **`sops`**
- 44 vectors over 7 synthetic SOP documents; access filter
  `access_classification ∈ {internal-operations, internal-demo}`; min score 0.35; top-k 3
- Retrieval is **server-side only** (gateway tools call Pinecone with the
  server-held key); retrieved text is untrusted data (directive 6A.5
  retrieval-guardrail treatment: injection screening, fixed index/namespace,
  no user-selectable namespaces)
- During P8-B local prototyping: the Pinecone client is **mocked** in tests and
  the live index is never written; the retrieval contract matches the existing
  index's parameters so staging can point at it without schema changes

## NVIDIA inference status

**Disabled.** `inference_enabled = false`. The local NeMo Python service is for
testing the guardrail pipeline only. Real NVIDIA NIM inference stays off until
(a) a secure DEPLOYED guardrail runtime exists and is verified, (b) NVIDIA
public-demo terms are confirmed, (c) explicit operator approval (P8-D). No
fabricated "live AI" is ever shown: the frontend labels modes honestly.
