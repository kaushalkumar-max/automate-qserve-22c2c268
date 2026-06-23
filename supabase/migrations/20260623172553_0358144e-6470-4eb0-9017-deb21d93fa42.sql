
DROP POLICY IF EXISTS "Open insert qserve_settings" ON public.qserve_settings;
DROP POLICY IF EXISTS "Open read qserve_settings" ON public.qserve_settings;
DROP POLICY IF EXISTS "Open update qserve_settings" ON public.qserve_settings;
DROP POLICY IF EXISTS "Open insert test_runs" ON public.test_runs;
DROP POLICY IF EXISTS "Open read test_runs" ON public.test_runs;
DROP POLICY IF EXISTS "Open update test_runs" ON public.test_runs;

ALTER TABLE public.qserve_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.test_runs ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.qserve_settings FROM anon, authenticated, PUBLIC;
REVOKE ALL ON public.test_runs FROM anon, authenticated, PUBLIC;

GRANT ALL ON public.qserve_settings TO service_role;
GRANT ALL ON public.test_runs TO service_role;
