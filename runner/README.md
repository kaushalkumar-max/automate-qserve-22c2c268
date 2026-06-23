# QServe Runner

External worker that executes QServe test runs on BrowserStack.

The Lovable dashboard inserts rows into `test_runs` with `status='queued'`.
This script polls that table, runs the matching Appium flow on a real
BrowserStack device, and writes step-by-step progress + screenshots back so
the dashboard's live view updates in real time.

## Setup

```bash
cd runner
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Environment

Export these before running:

```bash
export SUPABASE_URL="https://<your-project-ref>.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="<service-role-key>"   # from Lovable Cloud backend
export BROWSERSTACK_USERNAME="<bs-user>"
export BROWSERSTACK_ACCESS_KEY="<bs-key>"
```

The service-role key bypasses RLS — keep it on your machine/CI only.

## Run

```bash
python runner.py
```

Leave it running. Each time you click **Run Test** in the dashboard, the
runner picks up the job within ~5 seconds.

## Customizing test cases

`runner.py` ships with three flows: `login_logout`, `login_browse`, and
`login_book_logout`. The Appium selectors are placeholders — open
`runner.py` and edit the `_tap(...)` / `_wait(...)` calls inside each
`run_*` function to match your real APK's `resource-id`s. Use BrowserStack
App Live or `uiautomatorviewer` to discover them.

To add a new test case:
1. Add it to `src/lib/qserve-config.ts` in the Lovable project (key, name, step list).
2. Add a `run_<key>(driver, rec)` function in `runner.py`.
3. Register it in the `TEST_CASES` dict at the bottom.

## Deploying the runner

Anywhere with outbound HTTPS works: laptop, a small VPS, a GitHub Actions
self-hosted runner, or a Docker container on Fly.io / Render. It only needs
the four env vars above.
