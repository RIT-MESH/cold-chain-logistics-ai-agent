-- FIX MIGRATION v2: restore PostgREST base privileges + least-privilege
-- function access. Safe to rerun (idempotent GRANT/REVOKE statements).
--
-- WHAT THIS DOES:
--   1. GRANT SELECT on all 4 tables to anon + authenticated (PostgREST
--      requires base SELECT to attempt a query; RLS controls which rows
--      are actually returned — this is Supabase's designed security model)
--   2. REVOKE EXECUTE on quota functions from PUBLIC (blocks ALL roles,
--      including inherited access — not just anon/authenticated)
--   3. GRANT EXECUTE to service_role ONLY (the Edge Function's role)
--
-- WHAT THIS DOES NOT DO:
--   - Does NOT grant INSERT/UPDATE/DELETE to anon or authenticated
--     (all writes go through the Edge Function via service_role, or
--     through SECURITY DEFINER functions)
--   - Does NOT grant SELECT on usage_counters or global_budget content
--     (RLS with no policies returns zero rows for authenticated/anon)
--
-- SECURITY MODEL:
--   Browser → Supabase Edge Function (service_role) → quota functions
--   Authenticated users can SELECT app_users (own row only, via RLS)
--   Authenticated users can SELECT request_audit (own rows only, via RLS)
--   Authenticated users CANNOT call reserve_inference or release_inference
--   Authenticated users CANNOT read usage_counters or global_budget content
--   All writes are performed by service_role or SECURITY DEFINER functions

-- ═══════════════════════════════════════════════════════════════════
-- 1. Base SELECT privileges for PostgREST (RLS controls row access)
-- ═══════════════════════════════════════════════════════════════════

GRANT SELECT ON TABLE public.app_users TO anon, authenticated;
GRANT SELECT ON TABLE public.usage_counters TO anon, authenticated;
GRANT SELECT ON TABLE public.global_budget TO anon, authenticated;
GRANT SELECT ON TABLE public.request_audit TO anon, authenticated;

-- ═══════════════════════════════════════════════════════════════════
-- 2. Function EXECUTE: revoke from PUBLIC, grant to service_role ONLY
-- ═══════════════════════════════════════════════════════════════════

-- Revoke from PUBLIC (the default grant — removes inherited access
-- from all roles including anon, authenticated, and any custom roles)
REVOKE EXECUTE ON FUNCTION public.reserve_inference(UUID) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.release_inference(UUID) FROM PUBLIC;

-- Explicit re-revoke from anon and authenticated (defense in depth,
-- in case PUBLIC was somehow restored)
REVOKE EXECUTE ON FUNCTION public.reserve_inference(UUID) FROM anon;
REVOKE EXECUTE ON FUNCTION public.release_inference(UUID) FROM anon;
REVOKE EXECUTE ON FUNCTION public.reserve_inference(UUID) FROM authenticated;
REVOKE EXECUTE ON FUNCTION public.release_inference(UUID) FROM authenticated;

-- Grant to service_role ONLY (the Edge Function's role)
GRANT EXECUTE ON FUNCTION public.reserve_inference(UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.release_inference(UUID) TO service_role;

-- ═══════════════════════════════════════════════════════════════════
-- 3. Remove unnecessary INSERT/UPDATE/DELETE from authenticated
--    (was granted by default in Supabase — unnecessary and violates
--    least-privilege; the Edge Function uses service_role for all writes)
-- ═══════════════════════════════════════════════════════════════════

REVOKE INSERT, UPDATE, DELETE ON TABLE public.app_users FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.usage_counters FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.global_budget FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.request_audit FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.app_users FROM anon;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.usage_counters FROM anon;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.global_budget FROM anon;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.request_audit FROM anon;

-- ═══════════════════════════════════════════════════════════════════
-- VERIFICATION QUERIES (copy-paste into a new SQL Editor tab after running)
-- ═══════════════════════════════════════════════════════════════════

-- Check RLS is enabled on all tables:
-- SELECT c.relname, c.relrowsecurity AS rls_enabled
--   FROM pg_class c WHERE c.relname IN
--   ('app_users', 'usage_counters', 'global_budget', 'request_audit');

-- Check function EXECUTE permissions (should show service_role ONLY):
-- SELECT routine_name, grantee, privilege_type
--   FROM information_schema.routine_privileges
--   WHERE routine_name IN ('reserve_inference', 'release_inference')
--   ORDER BY routine_name, grantee;

-- Check table privileges (should show SELECT only for anon/authenticated):
-- SELECT table_name, grantee, privilege_type
--   FROM information_schema.table_privileges
--   WHERE table_name IN ('app_users', 'usage_counters', 'global_budget', 'request_audit')
--   AND grantee IN ('anon', 'authenticated')
--   ORDER BY table_name, grantee, privilege_type;
