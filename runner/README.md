# QServe Runner

External worker that executes QServe test runs on BrowserStack.

The Lovable dashboard queues a test run. This script asks the published app
for the next queued job, runs the matching Appium flow on a real BrowserStack
device, and posts step-by-step progress + screenshots back so the dashboard's
live view updates in real time.

## Setup

```bash
cd runner
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Environment

Export these before running:

```bash
export BROWSERSTACK_USERNAME="<bs-user>"
export BROWSERSTACK_ACCESS_KEY="<bs-key>"
# Optional. Defaults to the live QServe app:
export QSERVE_APP_URL="https://automate-qserve.lovable.app"
```

You do not need a backend URL, backend access key, service user, or sign-in
account for the runner. The app verifies the runner using the same
BrowserStack credentials that Render already has.

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
the BrowserStack env vars above.
