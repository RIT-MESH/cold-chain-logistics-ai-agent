# Operator Setup Guide: Google OAuth + Supabase Free (2026-11-22)

This is the exact configuration you must complete in your browser. Everything
here is free-tier only — no billing account required. Once you complete these
steps, I can run the staging authentication tests.

**Total time: approximately 10-15 minutes.**

---

## Part 1: Create a Supabase Free Project

1. Go to https://supabase.com/dashboard
2. Sign in (or create a free account)
3. Click **New Project**
4. Configure:
   - **Name:** `cold-chain-ai` (or any name you prefer)
   - **Database Password:** choose a strong password — save it securely
   - **Region:** select the region closest to you (e.g., `us-east-1` or `ap-northeast-1`)
   - **Plan:** Free
5. Wait ~2 minutes for the project to provision
6. Once ready, note these two values from **Settings → API**:
   - `Project URL` (looks like `https://xyz.supabase.co`)
   - `anon public` key (safe for the browser — NOT a secret)

---

## Part 2: Create a Google Cloud OAuth Client

1. Go to https://console.cloud.google.com
2. Sign in with your Google account
3. **Create a new project** (or use an existing one):
   - Name: `cold-chain-ai-auth` (or any name)
4. In the left menu, go to **APIs & Services → OAuth consent screen**
5. Configure:
   - **User type:** External
   - **App name:** `Cold-Chain Logistics AI Agent`
   - **User support email:** your email
   - **App logo:** (optional)
   - **Application home page:** your Hugging Face Space URL
   - **Privacy policy URL:** (any page with a privacy statement)
   - **Authorized domains:** add your Supabase project domain (e.g., `xyz.supabase.co`)
   - **Scopes:** you should see only `openid`, `email`, and `profile` — do NOT add Gmail, Contacts, or Drive scopes
6. Click **Save and Continue** through all steps
7. Go to **APIs & Services → Credentials**
8. Click **Create Credentials → OAuth client ID**
9. Configure:
   - **Application type:** Web application
   - **Name:** `Cold-Chain Supabase Auth`
   - **Authorized redirect URIs:** Copy the exact redirect URI from your
     Supabase dashboard (Authentication → Providers → Google → "Redirect URLs")
     — it looks like `https://xyz.supabase.co/auth/v1/callback`
   - **IMPORTANT:** Do NOT invent a redirect URI. Copy it exactly from Supabase.
10. Click **Create**
11. Note the **Client ID** and **Client Secret**

---

## Part 3: Enable Google Auth in Supabase

1. In your Supabase dashboard, go to **Authentication → Providers**
2. Find **Google** and click to expand
3. Toggle it **ON**
4. Paste your Google OAuth **Client ID** and **Client Secret** from Part 2
5. Click **Save**
6. Go to **Authentication → URL Configuration**
7. Set the **Site URL** to your Hugging Face Space URL
   (e.g., `https://ritmesh-cold-chain-logistics-ai.hf.space`)
8. Add the same URL to **Redirect URLs**

---

## Part 4: Apply the Database Migration

1. In your Supabase dashboard, go to **SQL Editor**
2. Click **New Query**
3. Copy the entire contents of `hf-space-v2/supabase/migrations/001_initial_gateway.sql`
   from this project and paste it into the editor
4. Click **Run**
5. Verify it creates these tables: `app_users`, `usage_counters`, `global_budget`, `request_audit`
6. Verify it creates these functions: `reserve_inference`, `release_inference`

---

## Part 5: Tell Me the Values

After completing Parts 1-4, share with me:

| What to share | Where to find it | Safe to share? |
|---|---|---|
| Supabase **Project URL** | Settings → API | Yes (public) |
| Supabase **anon public key** | Settings → API | Yes (public — designed for the browser) |
| Confirmation that the migration ran | SQL Editor | Yes |

**Do NOT share:**
- Database password
- Google OAuth Client Secret
- Supabase service_role key
- Any NVIDIA API key

---

## What I Will Do After You Complete Setup

Once you share the Project URL and anon key, I will:

1. Deploy the Edge Function (`supabase functions deploy cold-chain-ai-gateway`)
2. Set the Edge Function secrets (NVIDIA key, NeMo auth secret, NeMo URL)
3. Run the staging authentication tests:
   - Google sign-in (real, via your Supabase project)
   - Session expiry and refresh
   - Account auto-registration (first sign-in creates `demo_user`)
   - Account suspension (`role='suspended'` → 403)
   - Direct API calls without auth → 401
   - Quota persistence across page refreshes and re-logins
   - Per-minute/hourly/daily rate limits → 429
   - Concurrency limits → 429
   - Emergency inference disable → 503
   - NeMo input/output guardrails → 400/502
   - Fail-closed when NeMo is unreachable → 503
4. Run the full red-team suite against the deployed gateway
5. Report actual test results with pass/fail counts

---

## What I Will NOT Do

- Access your Gmail, Contacts, or Drive
- Store any Google identity data beyond the user UID
- Deploy to GCP, AWS, or any paid platform
- Activate real NVIDIA inference without your explicit approval
- Publish to Hugging Face without your explicit approval
- Modify Pinecone, AWS, or the existing production environment
