"""
QServe Test Runner
------------------
Polls the QServe web app for queued jobs, executes them on BrowserStack via
Appium, and posts step/screenshot progress back to the app.

Required environment variables:
  BROWSERSTACK_USERNAME
  BROWSERSTACK_ACCESS_KEY

Optional:
  QSERVE_APP_URL   default https://automate-qserve.lovable.app
"""

from __future__ import annotations

import os
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

import requests
from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.common.actions import interaction

BS_USER = os.environ["BROWSERSTACK_USERNAME"]
BS_KEY  = os.environ["BROWSERSTACK_ACCESS_KEY"]
APP_BASE_URL = os.environ.get(
    "QSERVE_APP_URL",
    os.environ.get("APP_BASE_URL", "https://automate-qserve.lovable.app"),
).rstrip("/")

APP_PACKAGE  = "com.qart.qserve"
APP_ACTIVITY = "com.qart.qserve.MainActivity"
POLL_INTERVAL_SEC = 5
BS_HUB = f"https://{BS_USER}:{BS_KEY}@hub-cloud.browserstack.com/wd/hub"

LOGIN_X_PCT, LOGIN_Y_PCT = 0.50, 0.70
PHOTO_X, PHOTO_Y = 540, 1344
SIZE_VALUES = ["1"] * 7


# ---------- QServe app API ----------

def db_select_queued() -> dict | None:
    r = requests.get(f"{APP_BASE_URL}/api/public/runner-next",
                     auth=(BS_USER, BS_KEY), timeout=15)
    r.raise_for_status()
    return r.json().get("job")


def db_update(run_id: str, patch: dict) -> None:
    try:
        r = requests.patch(f"{APP_BASE_URL}/api/public/runner-update",
                           auth=(BS_USER, BS_KEY),
                           json={"run_id": run_id, "patch": patch}, timeout=15)
        if not r.ok:
            print(f"[db_update] {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[db_update] error {e}")


# ---------- BrowserStack session info ----------

def bs_session_info(session_id: str) -> dict:
    try:
        r = requests.get(
            f"https://api-cloud.browserstack.com/app-automate/sessions/{session_id}.json",
            auth=(BS_USER, BS_KEY), timeout=20)
        if r.ok:
            return r.json().get("automation_session", {}) or {}
    except Exception as e:
        print(f"[bs_session_info] {e}")
    return {}


# ---------- Step recorder ----------

class StepRecorder:
    def __init__(self, run_id: str, step_names: list[str]):
        self.run_id = run_id
        self.step_names = step_names
        self.session_id = None
        self.steps: list[dict] = []
        self.started_at = time.time()
        self.failure_screenshot: str | None = None

    def _push(self):
        db_update(self.run_id, {
            "steps": self.steps,
            "screenshots": [self.failure_screenshot] if self.failure_screenshot else [],
        })

    def begin(self, idx: int):
        name = self.step_names[idx] if idx < len(self.step_names) else f"Step {idx+1}"
        db_update(self.run_id, {
            "status": "running",
            "current_step_index": idx,
            "current_step_name": name,
            "session_id": self.session_id,
            "message": f"Step {idx + 1}/{len(self.step_names)}: {name}",
        })

    def pass_(self, idx: int):
        name = self.step_names[idx] if idx < len(self.step_names) else f"Step {idx+1}"
        self.steps.append({
            "index": idx, "name": name, "status": "pass", "passed": True,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        self._push()

    def fail(self, idx: int, driver, error: str):
        name = self.step_names[idx] if idx < len(self.step_names) else f"Step {idx+1}"
        shot = None
        if driver is not None:
            try:
                shot = "data:image/png;base64," + driver.get_screenshot_as_base64()
                self.failure_screenshot = shot
            except Exception:
                pass
        self.steps.append({
            "index": idx, "name": name, "status": "fail", "passed": False,
            "error": error, "screenshot": shot,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        self._push()

    def finalize(self, driver, passed: bool, message: str):
        info = bs_session_info(self.session_id) if self.session_id else {}
        db_update(self.run_id, {
            "status": "passed" if passed else "failed",
            "passed": passed,
            "message": message,
            "duration_seconds": int(time.time() - self.started_at),
            "public_url": info.get("public_url"),
            "video_url": info.get("video_url"),
        })


# ---------- Driver ----------

def make_driver(run: dict) -> webdriver.Remote:
    opts = UiAutomator2Options()
    opts.platform_name = "Android"
    opts.platform_version = run.get("os_version") or "13.0"
    opts.device_name = run.get("device") or "Samsung Galaxy S23"
    opts.app = run["app_url"]
    opts.app_package = APP_PACKAGE
    opts.app_activity = APP_ACTIVITY
    opts.auto_grant_permissions = True
    opts.set_capability("appium:newCommandTimeout", 240)
    opts.set_capability("appium:appWaitActivity", "*")
    opts.set_capability("appium:forceAppLaunch", True)

    bstack = {
        "projectName": "QServe",
        "buildName": run.get("build_name") or "QServe Build",
        "sessionName": run.get("test_case_name") or run["test_case_key"],
        "userName": BS_USER,
        "accessKey": BS_KEY,
        "debug": True,
        "video": True,
        "networkLogs": True,
        "deviceLogs": True,
        "deviceOrientation": "portrait",
        "disableAnimations": "true",
        "enableShellCommands": "true",
    }

    qr_media = run.get("qr_media_url")
    if qr_media:
        # BrowserStack media injection — file appears in device gallery.
        # Must be a top-level capability, NOT inside bstack:options.
        opts.set_capability("browserstack.media", [qr_media])
    opts.set_capability("bstack:options", bstack)

    return webdriver.Remote(BS_HUB, options=opts)


# ---------- Action helpers ----------

def tap_pct(driver, x_pct, y_pct):
    s = driver.get_window_size()
    driver.execute_script("mobile: clickGesture",
                          {"x": int(s["width"] * x_pct), "y": int(s["height"] * y_pct)})

def tap_xy(driver, x, y):
    driver.execute_script("mobile: clickGesture", {"x": x, "y": y})

def ensure_app_open(driver):
    try:
        driver.activate_app(APP_PACKAGE)
        WebDriverWait(driver, 8).until(lambda d: d.current_package == APP_PACKAGE)
        return
    except Exception:
        pass
    driver.execute_script("mobile: startActivity",
                          {"component": f"{APP_PACKAGE}/{APP_ACTIVITY}"})
    WebDriverWait(driver, 15).until(lambda d: d.current_package == APP_PACKAGE)

def try_click(driver, locators, timeout=3) -> bool:
    for by, val in locators:
        try:
            WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, val))).click()
            return True
        except Exception:
            continue
    return False

def draw_signature(driver):
    pad = WebDriverWait(driver, 30).until(EC.presence_of_element_located((
        AppiumBy.XPATH,
        "//android.widget.FrameLayout[@resource-id='android:id/content']"
        "/android.widget.FrameLayout/android.view.View/android.view.View"
        "/android.view.View/android.view.View/android.view.View[2]"
    )))
    loc, size = pad.location, pad.size
    sx, sy = int(loc["x"] + size["width"] * 0.2), int(loc["y"] + size["height"] * 0.2)
    ex, ey = int(loc["x"] + size["width"] * 0.8), int(loc["y"] + size["height"] * 0.8)
    touch = PointerInput(interaction.POINTER_TOUCH, "finger")
    a = ActionBuilder(driver, mouse=touch)
    a.pointer_action.move_to_location(sx, sy)
    a.pointer_action.pointer_down()
    a.pointer_action.move_to_location(ex, ey)
    a.pointer_action.pointer_up()
    a.perform()
    time.sleep(1)


# ---------- Full booking flow (20 steps) ----------

def step_open_app(driver):     ensure_app_open(driver); time.sleep(2)
def step_scan_qr(driver):
    WebDriverWait(driver, 30).until(EC.element_to_be_clickable(
        (AppiumBy.ACCESSIBILITY_ID, "Scan QR from gallery"))).click()
def step_picker_open(driver):
    WebDriverWait(driver, 20).until(lambda d: "photopicker" in d.current_package.lower())
    time.sleep(0.5)
def step_tap_photo(driver):    tap_xy(driver, PHOTO_X, PHOTO_Y); time.sleep(0.8)
def step_done_picker(driver):
    if not try_click(driver, [
        (AppiumBy.XPATH, "//*[@text='Done']"),
        (AppiumBy.ACCESSIBILITY_ID, "Done"),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches("(?i)done")'),
    ], timeout=2):
        tap_pct(driver, 0.86, 0.955)
def step_return_app(driver):
    try:
        WebDriverWait(driver, 15).until(lambda d: d.current_package == APP_PACKAGE)
    except Exception:
        driver.back()
        WebDriverWait(driver, 12).until(lambda d: d.current_package == APP_PACKAGE)
def step_tap_login(driver):    tap_pct(driver, LOGIN_X_PCT, LOGIN_Y_PCT); time.sleep(4)
def step_wait_home(driver):    time.sleep(2)
def step_logout(driver):
    WebDriverWait(driver, 20).until(EC.element_to_be_clickable(
        (AppiumBy.ACCESSIBILITY_ID, "Logout"))).click()

def step_catalogue(driver):
    if not try_click(driver, [
        (AppiumBy.ACCESSIBILITY_ID, "Catalogue"),
        (AppiumBy.ACCESSIBILITY_ID, "Catalogue Tab"),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Catalogue")'),
    ]):
        tap_xy(driver, 888, 2219)
    time.sleep(0.8)

def step_brand_boys(driver):
    if not try_click(driver, [
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Boys")'),
        (AppiumBy.XPATH, '//*[@content-desc[contains(., "Boys")]]'),
    ], timeout=5):
        raise RuntimeError("Brand 'Boys' not found")

def step_first_product(driver):
    if not try_click(driver, [
        (AppiumBy.XPATH, '//android.widget.ImageView[@content-desc="BCO_CRYSTALIS B\n1999"]'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.ImageView").instance(0)'),
    ], timeout=5):
        raise RuntimeError("Product not found")
    time.sleep(0.8)

def step_fill_sizes(driver):
    inputs = WebDriverWait(driver, 15).until(
        EC.presence_of_all_elements_located((AppiumBy.CLASS_NAME, "android.widget.EditText")))
    entered = 0
    for i, inp in enumerate(inputs):
        try:
            inp.click(); inp.clear()
            inp.set_value(SIZE_VALUES[i] if i < len(SIZE_VALUES) else "1")
            entered += 1; time.sleep(0.1)
        except Exception:
            pass
    if entered == 0:
        raise RuntimeError("No size EditText fields found")
    try_click(driver, [(AppiumBy.ACCESSIBILITY_ID, "Dismiss")], timeout=2) or tap_xy(driver, 248, 2360)
    time.sleep(0.3)

def step_plus(driver):
    if not try_click(driver, [
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().className("android.widget.Button").text("+")'),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().className("android.widget.Button").instance(1)'),
    ]):
        tap_xy(driver, 887, 1919)
    time.sleep(0.5)

def step_add_to_cart(driver):
    if not try_click(driver, [
        (AppiumBy.XPATH, '//android.widget.ImageView[@content-desc="Add to cart"]'),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().description("Add to cart")'),
    ], timeout=5):
        tap_xy(driver, 679, 2360)
    time.sleep(0.8)

def step_home(driver):
    if not try_click(driver, [
        (AppiumBy.ACCESSIBILITY_ID, "Home"),
        (AppiumBy.ACCESSIBILITY_ID, "Home Tab"),
    ]):
        tap_xy(driver, 540, 2221)
    time.sleep(0.8)

def step_cart_tab(driver):
    if not try_click(driver, [
        (AppiumBy.ACCESSIBILITY_ID, "Cart"),
        (AppiumBy.ACCESSIBILITY_ID, "Cart Tab"),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Cart")'),
    ]):
        tap_xy(driver, 935, 2219)
    time.sleep(0.8)

def step_save(driver):
    WebDriverWait(driver, 20).until(EC.element_to_be_clickable(
        (AppiumBy.ACCESSIBILITY_ID, "SAVE"))).click()
    time.sleep(0.8)

def step_signature(driver):    draw_signature(driver)

def step_submit(driver):
    WebDriverWait(driver, 20).until(EC.element_to_be_clickable(
        (AppiumBy.ACCESSIBILITY_ID, "Submit"))).click()

def step_wait_order(driver):   time.sleep(30)


LOGIN_LOGOUT = [
    step_open_app, step_scan_qr, step_picker_open, step_tap_photo,
    step_done_picker, step_return_app, step_tap_login, step_wait_home, step_logout,
]

LOGIN_BROWSE = [
    step_open_app, step_scan_qr, step_picker_open, step_tap_photo,
    step_done_picker, step_return_app, step_tap_login, step_wait_home,
    step_catalogue, step_brand_boys, step_logout,
]

LOGIN_BOOK_LOGOUT = [
    step_open_app, step_scan_qr, step_picker_open, step_tap_photo,
    step_done_picker, step_return_app, step_tap_login, step_wait_home,
    step_catalogue, step_brand_boys, step_first_product, step_fill_sizes,
    step_plus, step_add_to_cart, step_home, step_cart_tab, step_save,
    step_signature, step_submit, step_wait_order, step_logout,
]

TEST_CASES: dict[str, list[Callable[[Any], None]]] = {
    "login_logout":      LOGIN_LOGOUT,
    "login_browse":      LOGIN_BROWSE,
    "login_book_logout": LOGIN_BOOK_LOGOUT,
}


# ---------- Execution ----------

def execute(run: dict) -> None:
    run_id = run["run_id"]
    tc_key = run["test_case_key"]
    step_names = run.get("step_names") or []
    print(f"[run {run_id}] starting {tc_key}")

    db_update(run_id, {"status": "starting", "message": "Provisioning device on BrowserStack…"})

    driver = None
    rec = StepRecorder(run_id, step_names)
    fns = TEST_CASES.get(tc_key)
    if not fns:
        rec.finalize(None, False, f"Unknown test_case_key: {tc_key}")
        return

    try:
        driver = make_driver(run)
        rec.session_id = driver.session_id
        db_update(run_id, {"session_id": rec.session_id})

        failed_idx = None
        for idx, fn in enumerate(fns):
            rec.begin(idx)
            try:
                fn(driver)
                rec.pass_(idx)
            except Exception as e:
                err = f"{type(e).__name__}: {str(e).splitlines()[0][:300]}"
                rec.fail(idx, driver, err)
                failed_idx = idx
                break

        if failed_idx is None:
            rec.finalize(driver, True, "All steps passed")
            print(f"[run {run_id}] passed")
        else:
            rec.finalize(driver, False,
                         f"Failed at step {failed_idx + 1}: "
                         f"{step_names[failed_idx] if failed_idx < len(step_names) else ''}")
            print(f"[run {run_id}] failed at step {failed_idx + 1}")
    except Exception as e:
        traceback.print_exc()
        rec.fail(len(rec.steps), driver, f"{type(e).__name__}: {e}")
        rec.finalize(driver, False, f"Runner error: {e}")
    finally:
        if driver is not None:
            try: driver.quit()
            except Exception: pass


def main() -> None:
    print(f"QServe runner online. Polling {APP_BASE_URL} for queued runs…")
    while True:
        try:
            job = db_select_queued()
            if job:
                execute(job)
            else:
                time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            print("Shutting down.")
            return
        except Exception as e:
            print(f"[loop] {e}")
            time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
