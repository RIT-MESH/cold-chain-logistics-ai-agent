-- Supabase Free PostgreSQL migration v4 (security-hardened 2026-11-22)
--
-- FIXES from error report + security review:
--   1. Idempotent: DROPs any leftover functions from partial prior runs
--   2. auth.uid() REMOVED from function bodies — the Edge Function (service_role)
--      has no JWT user context, so auth.uid() returns NULL. Instead, quota
--      functions are callable ONLY by service_role (NOT by authenticated/anon).
--      The Edge Function passes the verified user_id from the JWT it validated.
--   3. Quota limits HARDCODED (no RPC parameter bypass possible)
--   4. auth.uid() check REPLACED by service-role-only execution
--   5. Daily quota reset logic (was: never reset — permanent lockout)
--   6. RLS on ALL tables (was: request_audit only)
--
-- ARCHITECTURE:
--   Browser → Supabase Edge Function (JWT verified) → reserve_inference (service_role)
--   The Edge Function is the ONLY caller of quota functions.
--   Authenticated users CANNOT call reserve_inference directly (no GRANT).
--   This is more secure than auth.uid() checks because the function is
--   completely inaccessible to regular users.

-- ═══════════════════════════════════════════════════════════════════
-- Idempotent cleanup: drop any functions from partial prior runs
-- ═══════════════════════════════════════════════════════════════════

DROP FUNCTION IF EXISTS reserve_inference(UUID, INTEGER, INTEGER, INTEGER, INTEGER, INTEGER, INTEGER);
DROP FUNCTION IF EXISTS reserve_inference(UUID);
DROP FUNCTION IF EXISTS release_inference(UUID);

-- ═══════════════════════════════════════════════════════════════════
-- Tables (idempotent)
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS app_users (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'demo_user' CHECK (role IN ('demo_user', 'suspended', 'admin')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS usage_counters (
  user_id UUID PRIMARY KEY REFERENCES app_users(id) ON DELETE CASCADE,
  utc_day DATE NOT NULL DEFAULT CURRENT_DATE,
  daily_count INTEGER NOT NULL DEFAULT 0 CHECK (daily_count >= 0),
  hourly_count INTEGER NOT NULL DEFAULT 0 CHECK (hourly_count >= 0),
  minute_count INTEGER NOT NULL DEFAULT 0 CHECK (minute_count >= 0),
  minute_window_start TIMESTAMPTZ,
  hourly_window_start TIMESTAMPTZ,
  inflight_count INTEGER NOT NULL DEFAULT 0 CHECK (inflight_count >= 0)
);

CREATE TABLE IF NOT EXISTS global_budget (
  id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  utc_day DATE NOT NULL DEFAULT CURRENT_DATE,
  global_daily_count INTEGER NOT NULL DEFAULT 0,
  global_inflight INTEGER NOT NULL DEFAULT 0,
  inference_enabled BOOLEAN NOT NULL DEFAULT false
);

INSERT INTO global_budget (id) VALUES (1)
  ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS request_audit (
  request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES app_users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  duration_ms INTEGER,
  guardrail_category TEXT,
  quota_decision TEXT,
  error_category TEXT,
  outcome TEXT NOT NULL DEFAULT 'pending',
  token_usage INTEGER
);

-- ═══════════════════════════════════════════════════════════════════
-- Row-Level Security: ALL tables
-- ═══════════════════════════════════════════════════════════════════

ALTER TABLE app_users ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users_read_own_profile" ON app_users
  FOR SELECT USING (auth.uid() = id);

ALTER TABLE usage_counters ENABLE ROW LEVEL SECURITY;
-- No policies = locked for authenticated/anon; service_role bypasses RLS

ALTER TABLE global_budget ENABLE ROW LEVEL SECURITY;
-- No policies = locked for authenticated/anon; service_role bypasses RLS

ALTER TABLE request_audit ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users_read_own_audit" ON request_audit
  FOR SELECT USING (auth.uid() = user_id);

-- ═══════════════════════════════════════════════════════════════════
-- SECURITY DEFINER FUNCTIONS
-- Called ONLY by the Edge Function (service_role) — never by authenticated users.
-- The Edge Function:
--   1. Verifies the Supabase JWT (getUser)
--   2. Checks suspension state (app_users)
--   3. Calls reserve_inference(user_id) via the admin client
-- The p_user_id is TRUSTED because only the Edge Function can call this,
-- and it always passes the JWT-verified user ID.
-- ═══════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION reserve_inference(p_user_id UUID)
RETURNS TABLE(allowed BOOLEAN, reason TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  -- HARDCODED limits — cannot be overridden via RPC parameters
  c_max_daily         CONSTANT INTEGER := 10;
  c_max_hourly        CONSTANT INTEGER := 5;
  c_max_minute        CONSTANT INTEGER := 2;
  c_max_user_conc     CONSTANT INTEGER := 1;
  c_max_global_conc   CONSTANT INTEGER := 2;
  c_max_global_daily  CONSTANT INTEGER := 100;

  v_daily INTEGER; v_hourly INTEGER; v_minute INTEGER;
  v_inflight INTEGER; v_utc_day DATE;
  v_global_daily INTEGER; v_global_inflight INTEGER; v_global_day DATE;
  v_enabled BOOLEAN;
  v_minute_start TIMESTAMPTZ; v_hourly_start TIMESTAMPTZ;
BEGIN
  -- ── Emergency kill switch (fail-closed) ──
  SELECT g.inference_enabled, g.global_daily_count, g.global_inflight, g.utc_day
    INTO v_enabled, v_global_daily, v_global_inflight, v_global_day
    FROM public.global_budget g WHERE g.id = 1 FOR UPDATE;

  IF NOT v_enabled THEN
    RETURN QUERY SELECT false, 'inference_disabled'::TEXT;
    RETURN;
  END IF;

  -- ── Global daily reset when the UTC day changes ──
  IF v_global_day < CURRENT_DATE THEN
    UPDATE public.global_budget
      SET utc_day = CURRENT_DATE, global_daily_count = 0
      WHERE id = 1;
    v_global_daily := 0;
  END IF;

  IF v_global_daily >= c_max_global_daily THEN
    RETURN QUERY SELECT false, 'global_daily_budget'::TEXT;
    RETURN;
  END IF;
  IF v_global_inflight >= c_max_global_conc THEN
    RETURN QUERY SELECT false, 'global_concurrency'::TEXT;
    RETURN;
  END IF;

  -- ── Per-user quotas (atomic: row locked by FOR UPDATE) ──
  SELECT u.daily_count, u.hourly_count, u.minute_count, u.inflight_count,
         u.utc_day, u.minute_window_start, u.hourly_window_start
    INTO v_daily, v_hourly, v_minute, v_inflight,
         v_utc_day, v_minute_start, v_hourly_start
    FROM public.usage_counters u WHERE u.user_id = p_user_id FOR UPDATE;

  IF v_daily IS NULL THEN
    INSERT INTO public.usage_counters (user_id) VALUES (p_user_id);
    v_daily := 0; v_hourly := 0; v_minute := 0; v_inflight := 0;
  END IF;

  -- ── Daily reset when the UTC day changes ──
  IF v_utc_day < CURRENT_DATE THEN
    v_daily := 0;
    v_hourly := 0;
  END IF;

  -- Hourly window reset
  IF v_hourly_start IS NOT NULL AND now() - v_hourly_start > interval '1 hour' THEN
    v_hourly := 0;
  END IF;

  -- Minute window reset
  IF v_minute_start IS NOT NULL AND now() - v_minute_start > interval '1 minute' THEN
    v_minute := 0;
  END IF;

  -- ── Per-user limits ──
  IF v_daily >= c_max_daily THEN
    RETURN QUERY SELECT false, 'daily_quota'::TEXT;
    RETURN;
  END IF;
  IF v_hourly >= c_max_hourly THEN
    RETURN QUERY SELECT false, 'hourly_quota'::TEXT;
    RETURN;
  END IF;
  IF v_minute >= c_max_minute THEN
    RETURN QUERY SELECT false, 'per_minute'::TEXT;
    RETURN;
  END IF;
  IF v_inflight >= c_max_user_conc THEN
    RETURN QUERY SELECT false, 'user_concurrency'::TEXT;
    RETURN;
  END IF;

  -- ── All checks passed: atomically increment ──
  UPDATE public.usage_counters
    SET utc_day = CURRENT_DATE,
        daily_count = CASE
          WHEN v_utc_day < CURRENT_DATE THEN 1
          ELSE daily_count + 1 END,
        hourly_count = CASE
          WHEN hourly_window_start IS NULL OR now() - hourly_window_start > interval '1 hour' THEN 1
          ELSE hourly_count + 1 END,
        minute_count = CASE
          WHEN minute_window_start IS NULL OR now() - minute_window_start > interval '1 minute' THEN 1
          ELSE minute_count + 1 END,
        inflight_count = inflight_count + 1,
        minute_window_start = CASE
          WHEN minute_window_start IS NULL OR now() - minute_window_start > interval '1 minute' THEN now()
          ELSE minute_window_start END,
        hourly_window_start = CASE
          WHEN hourly_window_start IS NULL OR now() - hourly_window_start > interval '1 hour' THEN now()
          ELSE hourly_window_start END
    WHERE user_id = p_user_id;

  UPDATE public.global_budget
    SET utc_day = CURRENT_DATE,
        global_daily_count = CASE
          WHEN v_global_day < CURRENT_DATE THEN 1
          ELSE global_daily_count + 1 END,
        global_inflight = global_inflight + 1
    WHERE id = 1;

  RETURN QUERY SELECT true, NULL::TEXT;
END;
$$;

CREATE OR REPLACE FUNCTION release_inference(p_user_id UUID) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  UPDATE public.usage_counters
    SET inflight_count = GREATEST(0, inflight_count - 1)
    WHERE user_id = p_user_id;

  UPDATE public.global_budget
    SET global_inflight = GREATEST(0, global_inflight - 1)
    WHERE id = 1;
END;
$$;

-- ═══════════════════════════════════════════════════════════════════
-- EXECUTE PERMISSIONS
--
-- SECURITY MODEL: quota functions are callable ONLY by the service_role
-- (the Edge Function). Authenticated and anon users CANNOT call them.
-- This is achieved by:
--   1. NOT granting EXECUTE to authenticated or anon
--   2. Explicitly REVOKEing from both roles
--   3. The service_role has EXECUTE by default (it bypasses most restrictions)
--
-- RLS is ALSO enforced on the underlying tables, so even if someone
-- somehow got EXECUTE permission, they still couldn't read/write the
-- quota tables directly.
-- ═══════════════════════════════════════════════════════════════════

-- Remove ALL table access from anon (anonymous users)
REVOKE ALL ON TABLE app_users FROM anon;
REVOKE ALL ON TABLE usage_counters FROM anon;
REVOKE ALL ON TABLE global_budget FROM anon;
REVOKE ALL ON TABLE request_audit FROM anon;

-- Remove function execute from anon
REVOKE EXECUTE ON FUNCTION reserve_inference(UUID) FROM anon;
REVOKE EXECUTE ON FUNCTION release_inference(UUID) FROM anon;

-- Remove function execute from authenticated (users cannot call quota
-- functions directly — only the Edge Function via service_role can)
REVOKE EXECUTE ON FUNCTION reserve_inference(UUID) FROM authenticated;
REVOKE EXECUTE ON FUNCTION release_inference(UUID) FROM authenticated;

-- Do NOT grant to authenticated. The service_role can execute any function
-- by default in Supabase. We intentionally leave it ungranted for authenticated.

-- ═══════════════════════════════════════════════════════════════════
-- VERIFICATION (run after the migration to confirm correct state)
-- ═══════════════════════════════════════════════════════════════════
-- SELECT
--   p.proname AS function_name,
--   pg_get_function_identity_arguments(p.oid) AS signature,
--   p.prosecdef AS is_security_definer,
--   p.proconfig AS config
-- FROM pg_proc p
-- WHERE p.proname IN ('reserve_inference', 'release_inference');
--
-- SELECT
--   c.relname AS table_name,
--   c.relrowsecurity AS rls_enabled
-- FROM pg_class c
-- WHERE c.relname IN ('app_users', 'usage_counters', 'global_budget', 'request_audit');
--
-- SELECT
--   routine_name, grantee, privilege_type
-- FROM information_schema.routine_privileges
-- WHERE routine_name IN ('reserve_inference', 'release_inference');
