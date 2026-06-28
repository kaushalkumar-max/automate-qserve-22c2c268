## Diagnosis

Infra is already correct:

- **`Procfile`** runs `uvicorn main:app` as a long-running Render web process. `main.py` spawns the Appium runner loop in a background thread on startup — it never blocks the HTTP port, so Render won't kill it. No change needed.
- **Frontend already polls Supabase**, not the Python backend. `src/routes/run.$runId.tsx` calls `getStatus` every 1.5s, which reads `test_runs` from the database via a server function. The browser never holds an HTTP connection to Render during the 2–5 min test.
- **Runner → app communication is also poll-based.** `runner.py` pulls jobs from `/api/public/runner-next` every 5s and pushes progress via `PATCH /api/public/runner-update`. No long-lived socket.

So timeouts are not what's stalling the run. The actual symptom we've been chasing in earlier turns is: **after the login step, the Appium session sits idle until BrowserStack's own idle-timeout kills it**, and the UI never advances past "Tap Login".

## What to actually fix

Make the post-login steps in `runner/runner.py` resilient instead of silently waiting:

1. **Heartbeat from the runner.** After every Appium action (tap, wait, screenshot), send a `runner-update` PATCH with `current_step_name` + a short `message`. BrowserStack idle-kills a session after ~90s with no command; emitting a `driver.get_screenshot_as_base64()` ping every ~30s inside long waits keeps the session alive AND gives the user live feedback.
2. **Bounded waits with explicit failure.** Replace any open-ended `WebDriverWait(..., 300)` after login with `WebDriverWait(..., 25)` wrapped in a retry that PATCHes `status="failed"` + a clear message ("Stuck after login: home screen never rendered") instead of hanging until BrowserStack times out.
3. **Post-login readiness probe.** After tapping Login, poll for one of: (a) bottom-nav Catalogue tab visible, (b) "Home"/greeting text, (c) known home-screen coordinate region non-blank. If none appear within 20s, capture a screenshot, log "login likely failed — QR rescan needed", and either trigger the existing rescan path or fail fast.
4. **Render-side safety net.** Add `--timeout-keep-alive 0` is not needed (we don't hold HTTP), but add a `/health` ping logger so we can confirm the runner thread is alive when a run looks stuck. (Already partly present — extend it to include "last job picked up at".)

No changes to `Procfile`, `main.py` polling architecture, or the frontend polling loop — those are already correct.

## Files to touch

- `runner/runner.py` — add heartbeat helper, bound the post-login waits, add readiness probe + fast-fail.
- `main.py` — extend `/health` to report `last_poll_at` and `last_job_id` (read from a module-level dict the runner updates).

No DB schema changes. No frontend changes.

Want me to go ahead with this, or do you want the heartbeat interval / fast-fail timeout tuned differently first?