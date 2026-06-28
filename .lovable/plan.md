## Answer

Both endpoints exist and are working as designed.

- `GET /api/public/runner-next` — `src/routes/api/public/runner-next.ts`. Live probe returns `401 Unauthorized` without credentials, which is correct: the route is gated by HTTP Basic using `BROWSERSTACK_USERNAME` / `BROWSERSTACK_ACCESS_KEY` (timing-safe compare). With those creds, it atomically claims the next `status='queued'` row in `test_runs`, flips it to `starting`, attaches the QR `media_url`, and returns the job.
- `PATCH /api/public/runner-update` — `src/routes/api/public/runner-update.ts`. Same Basic-auth gate. Accepts `{ run_id, patch }`, whitelists patch fields, stamps `updated_at`, writes via the admin client.

Both live under `/api/public/*`, so Lovable's published-site auth gate is bypassed and they're reachable from anywhere via `https://automate-qserve.lovable.app/api/public/...`.

## Where `runner.py` runs

Outside Lovable, as a separate always-on process. Lovable hosts the dashboard (TanStack Start on Cloudflare Workers) plus the two endpoints above; it can't host a long-running Python Appium polling loop. Your repo is already set up for Render:

- `Procfile`: `web: uvicorn main:app --host 0.0.0.0 --port $PORT`
- `main.py` boots FastAPI and spawns `runner.main()` in a daemon thread on startup, so Render keeps the web service alive on the HTTP port while the runner loop polls `/api/public/runner-next` every 5s.
- Render env vars required: `BROWSERSTACK_USERNAME`, `BROWSERSTACK_ACCESS_KEY`. Optional: `QSERVE_APP_URL` (defaults to `https://automate-qserve.lovable.app`).

Liveness check: `https://qserve-test-manager.onrender.com/health` returns `runner.last_poll_at`, `last_job_id`, `last_step`, `last_heartbeat_at`. If `last_poll_at` keeps advancing, the runner is alive and consuming the endpoints.

## Optional improvement (only if you want it)

Add a small **Runner status** card to the dashboard that fetches Render's `/health` every ~10s and shows a green/red dot plus "last poll Xs ago". Files touched:

- `src/lib/qserve.functions.ts` — new `getRunnerHealth` server fn that fetches `${process.env.RENDER_URL}/health` (or a hardcoded `https://qserve-test-manager.onrender.com/health`) and returns the JSON.
- `src/components/qserve/RunnerStatus.tsx` — new component, polls every 10s via `useQuery`, renders pill on the index page.
- `src/routes/index.tsx` — mount the pill next to the existing header.

No backend or runner changes. Tell me yes/no on this widget and I'll build it.