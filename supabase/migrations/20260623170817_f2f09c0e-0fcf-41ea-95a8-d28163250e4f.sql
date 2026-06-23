
CREATE TABLE public.test_runs (
  run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  status TEXT NOT NULL DEFAULT 'queued',
  test_case_key TEXT NOT NULL,
  test_case_name TEXT NOT NULL,
  build_name TEXT,
  device TEXT,
  device_id TEXT,
  os_version TEXT,
  app_url TEXT NOT NULL,
  steps_total INT DEFAULT 0,
  step_names JSONB DEFAULT '[]'::jsonb,
  steps JSONB DEFAULT '[]'::jsonb,
  screenshots JSONB DEFAULT '[]'::jsonb,
  session_id TEXT,
  video_url TEXT,
  public_url TEXT,
  duration_seconds INT DEFAULT 0,
  passed BOOLEAN,
  message TEXT,
  current_step_index INT DEFAULT 0,
  current_step_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON public.test_runs TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.test_runs TO anon;
GRANT ALL ON public.test_runs TO service_role;
ALTER TABLE public.test_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Open read test_runs" ON public.test_runs FOR SELECT USING (true);
CREATE POLICY "Open insert test_runs" ON public.test_runs FOR INSERT WITH CHECK (true);
CREATE POLICY "Open update test_runs" ON public.test_runs FOR UPDATE USING (true) WITH CHECK (true);
CREATE INDEX test_runs_created_at_idx ON public.test_runs (created_at DESC);

CREATE TABLE public.qserve_settings (
  key TEXT PRIMARY KEY,
  media_url TEXT,
  filename TEXT,
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON public.qserve_settings TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.qserve_settings TO anon;
GRANT ALL ON public.qserve_settings TO service_role;
ALTER TABLE public.qserve_settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Open read qserve_settings" ON public.qserve_settings FOR SELECT USING (true);
CREATE POLICY "Open insert qserve_settings" ON public.qserve_settings FOR INSERT WITH CHECK (true);
CREATE POLICY "Open update qserve_settings" ON public.qserve_settings FOR UPDATE USING (true) WITH CHECK (true);
