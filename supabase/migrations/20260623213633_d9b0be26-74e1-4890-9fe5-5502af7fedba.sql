-- Allow authenticated users (including the dedicated runner account) to read and update test_runs
GRANT SELECT, INSERT, UPDATE ON public.test_runs TO authenticated;
GRANT ALL ON public.test_runs TO service_role;

ALTER TABLE public.test_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated can read test_runs" ON public.test_runs;
CREATE POLICY "Authenticated can read test_runs"
  ON public.test_runs FOR SELECT
  TO authenticated
  USING (true);

DROP POLICY IF EXISTS "Authenticated can insert test_runs" ON public.test_runs;
CREATE POLICY "Authenticated can insert test_runs"
  ON public.test_runs FOR INSERT
  TO authenticated
  WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated can update test_runs" ON public.test_runs;
CREATE POLICY "Authenticated can update test_runs"
  ON public.test_runs FOR UPDATE
  TO authenticated
  USING (true)
  WITH CHECK (true);
