"""
FastAPI wrapper for the QServe test runner.

Render's Web Service expects an HTTP port to stay open. We expose a tiny
health endpoint and start the polling runner loop in a background thread.
If you switch the Render service type to "Background Worker", you can
instead use `python runner/runner.py` as the start command and ignore
this file.
"""

import threading
import sys
import os

from fastapi import FastAPI

# Make `runner/` importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "runner"))

app = FastAPI(title="QServe Runner")

_runner_started = False
_runner_lock = threading.Lock()


def _start_runner_once() -> None:
    global _runner_started
    with _runner_lock:
        if _runner_started:
            return
        _runner_started = True

        def _loop():
            try:
                from runner import main as runner_main  # runner/runner.py
                runner_main()
            except Exception as exc:
                print(f"[runner thread crashed] {exc}", flush=True)

        t = threading.Thread(target=_loop, name="qserve-runner", daemon=True)
        t.start()
        print("[main] runner thread started", flush=True)


@app.on_event("startup")
def _on_startup() -> None:
    _start_runner_once()


@app.get("/")
def root():
    return {"service": "qserve-runner", "status": "ok"}


@app.get("/health")
def health():
    status = {"status": "ok", "runner_started": _runner_started}
    try:
        from runner import RUNNER_STATUS  # type: ignore
        status["runner"] = RUNNER_STATUS
    except Exception as exc:
        status["runner_error"] = str(exc)
    return status
