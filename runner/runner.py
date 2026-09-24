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
import re
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

BS_USER = os.environ["BROWSERSTACK_USERNAME"].strip()
BS_KEY  = os.environ["BROWSERSTACK_ACCESS_KEY"].strip()
APP_BASE_URL = os.environ.get(
    "QSERVE_APP_URL",
    os.environ.get("APP_BASE_URL", "https://automate-qserve.lovable.app"),
).rstrip("/")

APP_PACKAGE  = "com.qart.qserve"
APP_ACTIVITY = "com.qart.qserve.MainActivity"
POLL_INTERVAL_SEC = 5
BS_HUB = f"https://{BS_USER}:{BS_KEY}@hub-cloud.browserstack.com/wd/hub"

# Runtime status, surfaced by main.py /health for "is the runner alive?" checks.
RUNNER_STATUS: dict = {
    "last_poll_at": None,
    "last_job_id": None,
    "last_step": None,
    "last_heartbeat_at": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def heartbeat(driver, run_id: str | None = None, message: str | None = None) -> None:
    """Keep the Appium session alive AND surface progress to the DB.

    BrowserStack idle-kills sessions after ~90s of no commands. A cheap
    page_source / get_window_size call resets the idle timer; we also PATCH
    the run row so the dashboard shows the loop is alive.
    """
    RUNNER_STATUS["last_heartbeat_at"] = _now_iso()
    if driver is not None:
        try:
            driver.get_window_size()
        except Exception:
            try:
                driver.page_source  # noqa: B018
            except Exception:
                pass
    if run_id and message:
        db_update(run_id, {"message": message})


def wait_until(driver, predicate, timeout: float, run_id: str | None = None,
               message: str | None = None, heartbeat_every: float = 25.0) -> bool:
    """Bounded wait that emits a heartbeat every ~25s. Returns True if predicate holds."""
    deadline = time.time() + timeout
    next_beat = time.time() + heartbeat_every
    while time.time() < deadline:
        try:
            if predicate(driver):
                return True
        except Exception:
            pass
        if time.time() >= next_beat:
            heartbeat(driver, run_id, message)
            next_beat = time.time() + heartbeat_every
        time.sleep(0.5)
    return False

LOGIN_X_PCT, LOGIN_Y_PCT = 0.50, 0.70
# Android photo picker fallback: first thumbnail in the Recent grid.
# Normal flow uses element bounds; these are only used if Android exposes no
# usable thumbnail nodes. On Pixel 8 screenshots the first QR tile center is
# ~x=190,y=850; the previous y=580 landed above the thumbnail row.
PHOTO_X_PCT, PHOTO_Y_PCT = 190 / 1080, 850 / 2400
PIXEL8_QR_TAP_X, PIXEL8_QR_TAP_Y = 190, 850
QR_IMAGE_TAP_X_PCT, QR_IMAGE_TAP_Y_PCT = 0.50, 0.50
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
    }


    qr_media = run.get("qr_media_url")
    if qr_media:
        # BrowserStack media injection — uploaded file appears in device gallery.
        # W3C: use `uploadMedia` inside bstack:options (array of media:// URLs).
        bstack["uploadMedia"] = [qr_media]
    opts.set_capability("bstack:options", bstack)

    return webdriver.Remote(BS_HUB, options=opts)


# ---------- Action helpers ----------

def force_portrait(driver):
    """Snap device back to portrait. Cheap; called before every step."""
    try:
        if driver.orientation != "PORTRAIT":
            driver.orientation = "PORTRAIT"
    except Exception:
        pass
    for cmd in (
        ["settings", "put", "system", "accelerometer_rotation", "0"],
        ["settings", "put", "system", "user_rotation", "0"],
    ):
        try:
            driver.execute_script("mobile: shell", {"command": cmd[0], "args": cmd[1:]})
        except Exception:
            pass


def scan_media(driver):
    """Force MediaStore to re-index sdcard so BrowserStack-injected files
    show up in the system Photo Picker / Gallery immediately."""
    cmds = [
        ["am", "broadcast", "-a", "android.intent.action.MEDIA_MOUNTED",
         "-d", "file:///sdcard", "--receiver-include-background"],
        ["content", "call", "--uri", "content://media",
         "--method", "scan_volume", "--arg", "external_primary"],
        ["cmd", "media_session", "scan"],
    ]
    for c in cmds:
        try:
            driver.execute_script("mobile: shell", {"command": c[0], "args": c[1:]})
        except Exception:
            pass
    time.sleep(2)


def tap_pct(driver, x_pct, y_pct):
    s = driver.get_window_size()
    tap_xy(driver, int(s["width"] * x_pct), int(s["height"] * y_pct))


def tap_xy(driver, x, y):
    """Tap absolute coords using W3C pointer actions (universally supported)."""
    finger = PointerInput(interaction.POINTER_TOUCH, "finger")
    actions = ActionBuilder(driver, mouse=finger)
    actions.pointer_action.move_to_location(int(x), int(y))
    actions.pointer_action.pointer_down()
    actions.pointer_action.pause(0.1)
    actions.pointer_action.pointer_up()
    actions.perform()


def tap_absolute(driver, x, y):
    """Tap exact app-screen coordinates using the same clickGesture path as the proven local script."""
    try:
        driver.execute_script("mobile: clickGesture", {"x": int(x), "y": int(y)})
    except Exception:
        tap_xy(driver, x, y)


def tap_xy_once(driver, x, y):
    """Tap absolute coords, preferring Appium's native click gesture.

    Some Android 14 photo picker surfaces ignore W3C pointer taps on grid
    cells. Do not send both gestures, because a double tap can select then
    immediately deselect the thumbnail.
    """
    try:
        driver.execute_script("mobile: clickGesture", {"x": int(x), "y": int(y)})
    except Exception:
        tap_xy(driver, x, y)


def tap_element_center(driver, el) -> bool:
    try:
        loc, size = el.location, el.size
        if size.get("width", 0) < 8 or size.get("height", 0) < 8:
            return False
        tap_xy(driver,
               int(loc["x"] + size["width"] / 2),
               int(loc["y"] + size["height"] / 2))
        return True
    except Exception:
        return False


def tap_inside_qr_image_bounds(driver, x1: int, y1: int, x2: int, y2: int) -> bool:
    """Tap the QR body, slightly left of center, within a detected image bound."""
    try:
        width, height = x2 - x1, y2 - y1
        if width < 8 or height < 8:
            return False
        tap_xy(driver,
               int(x1 + width * QR_IMAGE_TAP_X_PCT),
               int(y1 + height * QR_IMAGE_TAP_Y_PCT))
        return True
    except Exception:
        return False

def tap_first_picker_thumbnail(driver, timeout=8) -> bool:
    """Tap the first real media thumbnail in the Android picker grid.

    Do not use a fixed center-screen coordinate here: on Galaxy S23 the QR is
    column 1 / row 1 of the Recent grid, around x=180,y=920, while the old
    center tap landed in blank/preview space. We prefer element bounds and only
    let the caller fall back to the Galaxy coordinate if no thumbnail is found.
    """
    locators = [
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("androidx.recyclerview.widget.RecyclerView")'
         '.childSelector(new UiSelector().className("android.widget.ImageView").instance(0))'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().resourceIdMatches(".*:id/icon_thumbnail").instance(0)'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().resourceIdMatches(".*:id/picker_item_thumbnail").instance(0)'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.ImageView").clickable(true).instance(0)'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.ImageView").descriptionMatches(".+").instance(0)'),
        (AppiumBy.XPATH, '//*[contains(@resource-id,"icon_thumbnail") or contains(@resource-id,"picker_item_thumbnail")]'),
        (AppiumBy.XPATH, '//androidx.recyclerview.widget.RecyclerView//*[@clickable="true"]'),
        (AppiumBy.XPATH, '//android.widget.ImageView'),
    ]
    screen = driver.get_window_size()
    min_y = int(screen["height"] * 0.25)
    max_y = int(screen["height"] * 0.62)
    max_x = int(screen["width"] * 0.48)

    deadline = time.time() + timeout
    while time.time() < deadline:
        for by, val in locators:
            try:
                elements = driver.find_elements(by, val)
                candidates = []
                for el in elements:
                    try:
                        loc, size = el.location, el.size
                        cx = int(loc["x"] + size["width"] / 2)
                        cy = int(loc["y"] + size["height"] / 2)
                        if (size.get("width", 0) >= 40 and size.get("height", 0) >= 40
                                and 0 <= cx <= max_x and min_y <= cy <= max_y):
                            candidates.append((cy, cx, el))
                    except Exception:
                        continue
                for _, _, el in sorted(candidates, key=lambda item: (item[0], item[1])):
                    if tap_element_center(driver, el):
                        return True
            except Exception:
                continue

        try:
            source = driver.page_source
            candidates = []
            for node in re.findall(r"<[^>]+>", source):
                lower = node.lower()
                if not any(key in lower for key in (
                        "icon_thumbnail", "picker_item_thumbnail", "thumbnail", "imageview", "item_root")):
                    continue
                m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
                if not m:
                    continue
                x1, y1, x2, y2 = map(int, m.groups())
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                if (x2 - x1) >= 40 and (y2 - y1) >= 40 and cx <= max_x and min_y <= cy <= max_y:
                    candidates.append((cy, cx))
            if candidates:
                cy, cx = sorted(candidates)[0]
                tap_xy(driver, cx, cy)
                return True
        except Exception:
            pass

        time.sleep(0.25)

    return False


def tap_visible_qr_thumbnail(driver) -> bool:
    """Tap the QR thumbnail, using dynamic bounds before coordinate fallback."""
    if tap_first_picker_thumbnail(driver, timeout=4):
        return True

    screen = driver.get_window_size()
    # Last resort for Galaxy S23 picker layout shown in the screenshot:
    # column 1 / row 1 of Recent images, not the dead center of the screen.
    tap_pct(driver, PHOTO_X_PCT, PHOTO_Y_PCT)
    return True


def picker_confirm_button(driver, timeout=1):
    """Return the visible Add/Done/Open/Select button after a media item is selected."""
    locators = [
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().resourceId("com.google.android.providers.media.module:id/button_add")'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().resourceIdMatches(".*:id/(button_add|button_done|done|confirm|action_button)")'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().textMatches("(?i)done|add|open|select")'),
        (AppiumBy.ACCESSIBILITY_ID, "Done"),
        (AppiumBy.ACCESSIBILITY_ID, "Add"),
        (AppiumBy.ACCESSIBILITY_ID, "Open"),
        (AppiumBy.ACCESSIBILITY_ID, "Select"),
    ]
    return wait_for_any(driver, locators, timeout=timeout)


def picker_has_selected_media(driver) -> bool:
    """Best-effort check that a thumbnail tap actually selected media."""
    if not picker_is_open(driver):
        return True

    btn = picker_confirm_button(driver, timeout=0.8)
    if btn is not None:
        try:
            return btn.is_enabled()
        except Exception:
            return True

    try:
        source = driver.page_source.lower()
        return any(marker in source for marker in (
            'checked="true"',
            'selected="true"',
            'content-desc="selected',
            'selected media',
            'button_add',
        ))
    except Exception:
        return False


def tap_and_confirm_qr_thumbnail(driver, x: int, y: int, wait_seconds=1.2) -> bool:
    tap_xy_once(driver, x, y)
    time.sleep(wait_seconds)
    return picker_has_selected_media(driver)


PICKER_PACKAGE_MARKERS = (
    "photopicker",
    "documentsui",
    "files",
    "providers.media.module",
    "mediaprovider",
)


PICKER_SURFACE_LOCATORS = [
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceId("android:id/media_tile")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceIdMatches(".*:id/(icon_thumbnail|image_thumbnail|media_tile|button_add)")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches("(?i)recent|photos|done|add")'),
    (AppiumBy.ACCESSIBILITY_ID, "Done"),
    (AppiumBy.ACCESSIBILITY_ID, "Add"),
]


def current_pkg(driver) -> str:
    """Foreground package name, resilient across Appium/UiAutomator2 versions.

    Newer appium-python-client maps driver.current_package to the
    `mobile: getCurrentPackage` extension, which older UiAutomator2 drivers on
    BrowserStack do not implement (UnknownMethodException). Fall back to a
    shell dumpsys query and finally to the page source root package attribute.
    """
    try:
        pkg = driver.current_package
        if pkg:
            return pkg
    except Exception:
        pass
    try:
        out = driver.execute_script("mobile: shell", {
            "command": "dumpsys",
            "args": ["window", "windows"],
        }) or ""
        m = re.search(r"mCurrentFocus=\S+\s+\S+\s+([A-Za-z0-9_.]+)/", str(out))
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        m = re.search(r'package="([^"]+)"', driver.page_source or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def is_picker_package(driver) -> bool:
    try:
        pkg = current_pkg(driver).lower()
        return bool(pkg) and any(marker in pkg for marker in PICKER_PACKAGE_MARKERS)
    except Exception:
        return False



def picker_is_open(driver) -> bool:
    return is_picker_package(driver) or has_any(driver, PICKER_SURFACE_LOCATORS, timeout=0.5)


def wait_for_any(driver, locators, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for by, val in locators:
            try:
                els = driver.find_elements(by, val)
                for el in els:
                    try:
                        if el.is_displayed():
                            return el
                    except Exception:
                        return el
            except Exception:
                continue
        time.sleep(0.25)
    return None


def has_any(driver, locators, timeout=1) -> bool:
    return wait_for_any(driver, locators, timeout=timeout) is not None


LOGIN_LOCATORS = [
    (AppiumBy.ACCESSIBILITY_ID, "Login"),
    (AppiumBy.ACCESSIBILITY_ID, "Log in"),
    (AppiumBy.ACCESSIBILITY_ID, "LOGIN"),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionMatches("(?i).*log ?in.*|.*login.*")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches("(?i).*log ?in.*|.*login.*")'),
]

SCAN_QR_LOCATORS = [
    (AppiumBy.ACCESSIBILITY_ID, "Scan QR from gallery"),
    (AppiumBy.ACCESSIBILITY_ID, "Scan QR from Gallery"),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Scan QR")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Scan QR")'),
    (AppiumBy.XPATH, '//*[contains(@content-desc, "Scan QR") or contains(@text, "Scan QR")]'),
]

HOME_LOCATORS = [
    (AppiumBy.ACCESSIBILITY_ID, "Catalogue"),
    (AppiumBy.ACCESSIBILITY_ID, "Catalogue Tab"),
    (AppiumBy.ACCESSIBILITY_ID, "Logout"),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Catalogue")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Logout")'),
]


def ensure_app_open(driver):
    try:
        driver.activate_app(APP_PACKAGE)
        WebDriverWait(driver, 8).until(lambda d: current_pkg(d) == APP_PACKAGE)
        return
    except Exception:
        pass
    for launch in (
        lambda: driver.execute_script("mobile: startActivity",
                                      {"component": f"{APP_PACKAGE}/{APP_ACTIVITY}"}),
        lambda: driver.execute_script("mobile: shell", {
            "command": "am",
            "args": ["start", "-n", f"{APP_PACKAGE}/{APP_ACTIVITY}"],
        }),
        lambda: driver.execute_script("mobile: shell", {
            "command": "monkey",
            "args": ["-p", APP_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1"],
        }),
    ):
        try:
            launch()
        except Exception:
            continue
        try:
            WebDriverWait(driver, 15).until(lambda d: current_pkg(d) == APP_PACKAGE)
            return
        except Exception:
            continue
    # Last resort: if any app UI is rendering, let the flow continue instead of
    # hard-failing step 1 on a package-detection quirk.
    if not (driver.page_source or "").strip():
        raise RuntimeError("Could not bring the app to the foreground")

def try_click(driver, locators, timeout=3) -> bool:
    for by, val in locators:
        try:
            WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, val))).click()
            return True
        except Exception:
            continue
    return False


def tap_first_locator_center(driver, locators, timeout=3) -> bool:
    """Tap the center of the first displayed element, even if Appium does not mark it clickable."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for by, val in locators:
            try:
                for el in driver.find_elements(by, val):
                    try:
                        if el.is_displayed() and tap_element_center(driver, el):
                            return True
                    except Exception:
                        if tap_element_center(driver, el):
                            return True
            except Exception:
                continue
        time.sleep(0.2)
    return False


def tap_catalogue_from_source_bounds(driver) -> bool:
    """Fallback for Flutter/React Native views: parse XML bounds and tap the Catalogue nav node."""
    try:
        source = driver.page_source
        screen = driver.get_window_size()
        candidates = []
        for node in re.findall(r"<[^>]+>", source):
            lower = node.lower()
            if not any(key in lower for key in ("nav_catalogue", "catalogue", "catalog")):
                continue
            m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            width, height = x2 - x1, y2 - y1
            if width < 12 or height < 12:
                continue
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            # Prefer bottom-nav nodes over titles/list text with the same word.
            bottom_rank = 0 if cy >= screen["height"] * 0.72 else 1
            id_rank = 0 if "nav_catalogue" in lower else 1
            candidates.append((bottom_rank, id_rank, cy, cx))
        if not candidates:
            return False
        _, _, cy, cx = sorted(candidates)[0]
        tap_xy(driver, cx, cy)
        return True
    except Exception:
        return False


def tap_catalogue_coordinates(driver) -> bool:
    """Tap the Catalogue bottom-nav tab using screenshot-derived coordinates.

    The attached home screenshot shows five bottom-nav actions. The old x=216
    Pixel-8 coordinate is the Home tab (about 20% width), not Catalogue.
    Catalogue is the second visible icon at ~32.5% width and ~93% height.
    """
    W, H = _screen(driver)
    raw_points = [
        # Dynamic coords from the actual screenshot: Catalogue center is the
        # second bottom-nav icon, about x=108/334 and y=676/728.
        (int(W * 0.325), int(H * 0.928)),
        (int(W * 0.325), int(H * 0.950)),
        (int(W * 0.325), int(H * 0.900)),
        (int(W * 0.350), int(H * 0.928)),
        (int(W * 0.300), int(H * 0.928)),
        # Pixel 8 physical equivalents for 1080x2400 screenshots.
        (350, 2228),
        (350, 2280),
        (350, 2160),
        (380, 2228),
        (325, 2228),
    ]

    seen: set[tuple[int, int]] = set()
    for x, y in raw_points:
        point = (max(1, min(int(W - 2), int(x))), max(1, min(int(H - 2), int(y))))
        if point in seen:
            continue
        seen.add(point)
        try:
            tap_absolute(driver, point[0], point[1])
        except Exception:
            continue
        time.sleep(1.5)
        if catalogue_is_open(driver, timeout=2):
            return True
    return False


CATALOGUE_NAV_LOCATORS = [
    # Proven post-login catalogue locators from qserve_automation_v2.py.
    (AppiumBy.ID, "nav_catalogue"),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceId("nav_catalogue")'),
    (AppiumBy.XPATH, '//android.widget.ImageView[@resource-id="nav_catalogue"]'),
    (AppiumBy.ACCESSIBILITY_ID, "Catalogue"),
    (AppiumBy.ACCESSIBILITY_ID, "Catalogue Tab"),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Catalogue")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("catalog")'),
    (AppiumBy.ID, "com.qart.qserve:id/nav_catalogue"),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceId("com.qart.qserve:id/nav_catalogue")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceIdMatches(".*(:id/)?nav_catalogue$")'),
    (AppiumBy.XPATH, '//*[@resource-id="com.qart.qserve:id/nav_catalogue" or @resource-id="nav_catalogue"]'),
    (AppiumBy.XPATH, '//*[contains(@resource-id, "nav_catalogue")]'),
]


CATALOGUE_OPEN_LOCATORS = [
    (AppiumBy.XPATH, '//android.view.View[@content-desc="Boys\n281 options"]'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Boys")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Boys")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Brand")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Brand")'),
]


def catalogue_is_open(driver, timeout=2) -> bool:
    return has_any(driver, CATALOGUE_OPEN_LOCATORS, timeout=timeout)

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
    scan_media(driver)
    for _ in range(3):
        if try_click(driver, SCAN_QR_LOCATORS, timeout=4) or tap_first_locator_center(driver, SCAN_QR_LOCATORS, timeout=2):
            try:
                WebDriverWait(driver, 6).until(lambda d: picker_is_open(d))
                return
            except Exception:
                time.sleep(0.5)

    raise RuntimeError("Scan QR from gallery did not open the photo picker")

def step_picker_open(driver):
    WebDriverWait(driver, 20).until(lambda d: picker_is_open(d))
    time.sleep(0.5)
def step_tap_photo(driver):
    time.sleep(2)

    try:
        pkg = current_pkg(driver).lower()
    except Exception:
        pkg = ""

    size = driver.get_window_size()

    # DocumentsUI / AOSP file picker path.
    if "documentsui" in pkg or "files" in pkg:
        if not tap_visible_qr_thumbnail(driver):
            raise RuntimeError("QR thumbnail not found in picker")
        try:
            WebDriverWait(driver, 5).until(
                lambda d: (not is_picker_package(d)) or picker_has_selected_media(d)
            )
        except Exception as e:
            raise RuntimeError("QR image was not selected; picker stayed open after tapping thumbnail") from e
        time.sleep(1.0)
        return

    # Pixel 8 / Android 14 Photo Picker: the first QR thumbnail is top-left.
    # Appium often exposes decorative ImageViews first. From the provided
    # screenshot, the QR tile occupies roughly x=0..365,y=730..1100 on a
    # 1080x2400 Pixel 8, so tap near its center and verify selection.
    if "providers.media.module" in pkg or (size.get("width") == 1080 and size.get("height", 0) >= 2300):
        for x, y in (
            (PIXEL8_QR_TAP_X, PIXEL8_QR_TAP_Y),
            (int(size["width"] * PHOTO_X_PCT), int(size["height"] * PHOTO_Y_PCT)),
            (180, 900),
            (240, 830),
        ):
            if tap_and_confirm_qr_thumbnail(driver, x, y):
                return
        raise RuntimeError("QR image was not selected; refusing to press Back from the photo picker")

    # Try system photo picker resource IDs for Android 14 (Pixel 8).
    selectors = [
        'new UiSelector().resourceId("com.google.android.providers.media.module:id/icon_thumbnail").instance(0)',
        'new UiSelector().resourceId("com.google.android.providers.media.module:id/image_thumbnail").instance(0)',
        'new UiSelector().resourceId("android:id/media_tile").instance(0)',
        'new UiSelector().className("android.widget.ImageView").clickable(true).instance(0)',
        'new UiSelector().className("android.widget.ImageView").instance(1)',
    ]

    for sel in selectors:
        try:
            el = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, sel)
            el.click()
            time.sleep(1)
            if picker_has_selected_media(driver):
                return
        except Exception:
            continue

    # Fallback: dynamic bounds-based first thumbnail scan.
    if tap_first_picker_thumbnail(driver, timeout=4):
        time.sleep(1)
        if picker_has_selected_media(driver):
            return

    # Final exact coordinate fallback from the visible first QR tile.
    if tap_and_confirm_qr_thumbnail(driver, PIXEL8_QR_TAP_X, PIXEL8_QR_TAP_Y):
        return
    raise RuntimeError("QR image was not selected; refusing to press Back from the photo picker")


def step_done_picker(driver):
    if picker_is_open(driver) and not picker_has_selected_media(driver):
        raise RuntimeError("QR image is not selected; refusing to press Back or Add")

    if not picker_is_open(driver):
        WebDriverWait(driver, 12).until(lambda d: current_pkg(d) == APP_PACKAGE)
        time.sleep(2)
        return

    tried = False
    confirm = picker_confirm_button(driver, timeout=2)
    if confirm is not None:
        try:
            confirm.click()
            tried = True
        except Exception:
            if tap_element_center(driver, confirm):
                tried = True

    if not tried:
        # Confirm button fallback: bottom-right on Android photo picker.
        W, H = _screen(driver)
        tap_xy_once(driver, int(W * 0.84), int(H * 0.96))
    WebDriverWait(driver, 12).until(lambda d: current_pkg(d) == APP_PACKAGE)
    time.sleep(2)

def step_return_app(driver):
    WebDriverWait(driver, 18).until(lambda d: current_pkg(d) == APP_PACKAGE)
    if not has_any(driver, LOGIN_LOCATORS + HOME_LOCATORS, timeout=8):
        if has_any(driver, SCAN_QR_LOCATORS, timeout=1):
            return
        raise RuntimeError("Returned to app, but neither Login nor Home screen appeared")

def step_tap_login(driver):
    if has_any(driver, HOME_LOCATORS, timeout=2):
        return
    for attempt in range(2):
        if try_click(driver, LOGIN_LOCATORS, timeout=4) or tap_first_locator_center(driver, LOGIN_LOCATORS, timeout=1):
            time.sleep(4)
            return
        if attempt == 0 and has_any(driver, SCAN_QR_LOCATORS, timeout=1):
            step_scan_qr(driver)
            step_picker_open(driver)
            step_tap_photo(driver)
            step_done_picker(driver)
            time.sleep(2)
            continue
        raise RuntimeError("Login button did not appear after QR selection")

def step_wait_home(driver):
    # Adopted from working uploaded runner: don't gate on Catalogue/Logout
    # detection — the home screen renders reliably ~4s after the login tap.
    # Gating was causing false "stuck after login" failures even when the
    # home screen was actually visible.
    time.sleep(4)
def _screen(driver):
    s = driver.get_window_size()
    return s["width"], s["height"]

def _find_and_click(driver, label: str, timeout: int = 10) -> bool:
    """Flutter semantic label finder: ACCESSIBILITY_ID then text() fallback."""
    try:
        el = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, label))
        )
        el.click()
        return True
    except Exception:
        pass
    try:
        el = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable(
                (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{label}")')
            )
        )
        el.click()
        return True
    except Exception:
        return False

def step_logout(driver):
    # Adopted verbatim from working uploaded runner.
    el = WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, "Logout"))
    )
    el.click()
    time.sleep(2)

def _click_with_locators(driver, locators: list, timeout_each: float = 2.5) -> bool:
    """Try each (by, value) locator in order. Returns True on first success."""
    for by, val in locators:
        try:
            el = WebDriverWait(driver, timeout_each).until(
                EC.element_to_be_clickable((by, val))
            )
            el.click()
            return True
        except Exception:
            continue
    return False


def step_catalogue(driver):
    ok = _click_with_locators(driver, [
        (AppiumBy.ID, "nav_catalogue"),
        (AppiumBy.ID, f"{APP_PACKAGE}:id/nav_catalogue"),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().resourceId("nav_catalogue")'),
        (AppiumBy.XPATH,
         '//android.widget.ImageView[@resource-id="nav_catalogue"]'),
        (AppiumBy.ACCESSIBILITY_ID, "Catalogue"),
        (AppiumBy.ACCESSIBILITY_ID, "Catalogue Tab"),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().descriptionContains("Catalogue")'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().descriptionContains("catalog")'),
    ])
    if not ok:
        tap_xy(driver, 888, 2219)
    time.sleep(1)


def step_brand_boys(driver):
    ok = _click_with_locators(driver, [
        (AppiumBy.XPATH,
         '//android.view.View[@content-desc="Boys\n281 options"]'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().descriptionContains("Boys")'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().textContains("Boys")'),
    ])
    if not ok:
        raise RuntimeError("Boys brand not found")
    time.sleep(1)


def step_first_product(driver):
    tried = _click_with_locators(driver, [
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.ImageView")'
         '.descriptionMatches("(?s).+\\n.+")'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().descriptionMatches("(?s).+\\n.+")'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.ImageView")'
         '.descriptionMatches(".*[0-9]{3,4}$")'),
    ], timeout_each=2)
    if tried:
        time.sleep(1)
        return

    candidates = []
    try:
        candidates = driver.find_elements(
            AppiumBy.XPATH,
            '//android.widget.ImageView[@clickable="true"]'
            ' | //android.view.View[@clickable="true"]',
        )
    except Exception:
        pass
    grid_items = []
    for el in candidates:
        try:
            loc = el.location
            desc = el.get_attribute("content-desc") or ""
            if 350 < loc["y"] < 1900 and desc.strip():
                grid_items.append((loc["y"], loc["x"], el))
        except Exception:
            continue
    grid_items.sort(key=lambda t: (t[0], t[1]))
    if grid_items:
        try:
            grid_items[0][2].click()
            time.sleep(1)
            return
        except Exception:
            pass

    try:
        el = driver.find_element(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().className("android.widget.ImageView")'
            '.clickable(true).instance(0)',
        )
        el.click()
        time.sleep(1)
        return
    except Exception:
        pass

    raise RuntimeError("First product card not found in grid")


def step_fill_sizes(driver):
    edits = WebDriverWait(driver, 15).until(
        EC.presence_of_all_elements_located(
            (AppiumBy.CLASS_NAME, "android.widget.EditText")
        )
    )
    if not edits:
        raise RuntimeError("No EditText found")
    def _read_text(el):
        try:
            return (el.get_attribute("text") or el.text or "").strip()
        except Exception:
            return ""

    def _clear_field(el):
        el.click()
        time.sleep(0.1)
        try:
            el.clear()
        except Exception:
            pass
        time.sleep(0.1)
        still = _read_text(el)
        if still:
            try:
                driver.press_keycode(123)  # MOVE_END
                for _ in range(len(still) + 3):
                    driver.press_keycode(67)  # Backspace
            except Exception:
                pass
        time.sleep(0.1)

    for idx, ed in enumerate(edits, start=1):
        try:
            _clear_field(ed)

            entered = False
            for write_attempt in range(3):
                try:
                    ed.set_value("1")
                except Exception:
                    try:
                        ed.click()
                        driver.press_keycode(8)  # Android KEYCODE_1
                    except Exception:
                        try:
                            ed.send_keys("1")
                        except Exception:
                            pass

                time.sleep(0.15)
                if _read_text(ed) == "1":
                    entered = True
                    break

                # If it stayed blank or appended wrongly, clear and retry before
                # moving to the next size box.
                _clear_field(ed)

            if not entered:
                raise RuntimeError(f"Could not enter quantity 1 in size box {idx}")

            time.sleep(0.15)
        except Exception:
            raise

    time.sleep(0.3)
    dismissed = _click_with_locators(driver, [
        (AppiumBy.ACCESSIBILITY_ID, "Dismiss"),
        (AppiumBy.XPATH, '//*[@content-desc="Dismiss"]'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().description("Dismiss")'),
    ], timeout_each=1.5)
    if not dismissed:
        tap_xy(driver, 248, 2360)
    time.sleep(0.5)


def step_plus(driver):
    ok = _click_with_locators(driver, [
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.Button").text("+")'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.Button").instance(1)'),
        (AppiumBy.XPATH,
         '(//android.view.View[@content-desc="0"])[1]/android.widget.Button[2]'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.Button").instance(0)'),
    ])
    if not ok:
        tap_xy(driver, 887, 1919)
    time.sleep(0.6)


def step_add_to_cart(driver):
    ok = _click_with_locators(driver, [
        (AppiumBy.XPATH,
         '//android.widget.ImageView[@content-desc="Add to cart"]'),
        (AppiumBy.ACCESSIBILITY_ID, "Add to cart"),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().description("Add to cart")'),
    ])
    if not ok:
        tap_xy(driver, 679, 2360)
    time.sleep(1)


def step_home(driver):
    ok = _click_with_locators(driver, [
        (AppiumBy.XPATH,
         "//android.widget.FrameLayout[@resource-id='android:id/content']"
         "/android.widget.FrameLayout/android.view.View/android.view.View"
         "/android.view.View/android.view.View/android.widget.Button"),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.Button").instance(1)'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.Button").instance(0)'),
        (AppiumBy.ACCESSIBILITY_ID, "Home"),
        (AppiumBy.ACCESSIBILITY_ID, "Home Tab"),
    ])
    if not ok:
        tap_xy(driver, 540, 2221)
    time.sleep(1)


def step_cart_tab(driver):
    ok = _click_with_locators(driver, [
        (AppiumBy.ID, "nav_cart"),
        (AppiumBy.ID, f"{APP_PACKAGE}:id/nav_cart"),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().resourceId("nav_cart")'),
        (AppiumBy.XPATH,
         '//android.widget.ImageView[@resource-id="nav_cart"]'),
        (AppiumBy.ACCESSIBILITY_ID, "Cart"),
        (AppiumBy.ACCESSIBILITY_ID, "Cart Tab"),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().descriptionContains("Cart")'),
    ])
    if not ok:
        tap_xy(driver, 935, 2219)
    time.sleep(1)


def step_save(driver):
    el = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, "SAVE"))
    )
    el.click()
    time.sleep(1)


def step_signature(driver):    draw_signature(driver)


def step_submit(driver):
    el = WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, "Submit"))
    )
    el.click()


def step_wait_order(driver):   time.sleep(30)



LOGIN_LOGOUT = [
    step_open_app, step_scan_qr, step_picker_open, step_tap_photo,
    step_done_picker, step_return_app, step_tap_login, step_wait_home,
    step_catalogue, step_brand_boys, step_first_product, step_fill_sizes,
    step_plus, step_add_to_cart, step_home, step_cart_tab, step_save,
    step_signature, step_submit, step_wait_order, step_logout,
]

CART_SWIPE_X1, CART_SWIPE_Y1 = 843, 837   # unchanged from your script
CART_SWIPE_X2, CART_SWIPE_Y2 = 304, 827   # unchanged from your script

CART_DELETE_SELECTOR = 'new UiSelector().className("android.view.View").instance(17)'
CART_BACK_SELECTOR = 'new UiSelector().className("android.widget.Button").instance(0)'
CART_EXTRA_BACK_SELECTOR = 'new UiSelector().className("android.widget.ImageView").instance(2)'


def swipe_w3c_touch(driver, x1, y1, x2, y2):
    """Same W3C touch swipe you already proved works — unchanged."""
    touch = PointerInput(interaction.POINTER_TOUCH, "touch")
    actions = ActionBuilder(driver, mouse=touch)
    actions.pointer_action.move_to_location(int(x1), int(y1))
    actions.pointer_action.pointer_down()
    actions.pointer_action.move_to_location(int(x2), int(y2))
    actions.pointer_action.release()
    actions.perform()


def get_first_category_row(driver):
    """Unchanged from your script — finds the first numeric content-desc row."""
    screen = driver.get_window_size()
    H = screen["height"]
    top_limit = int(H * 0.15)
    bottom_limit = int(H * 0.88)
    try:
        candidates = driver.find_elements(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().className("android.view.View")'
        )
        for elem in candidates:
            desc = (elem.get_attribute("content-desc") or "").strip()
            if not desc or not desc.isdigit():
                continue
            try:
                y = elem.location["y"]
                if y < top_limit or y > bottom_limit:
                    continue
            except Exception:
                continue
            return elem
    except Exception:
        pass
    return None


def delete_all_options_in_category(driver):
    """Unchanged from your script — one swipe, then click delete until gone."""
    deleted = 0
    swipe_w3c_touch(driver, CART_SWIPE_X1, CART_SWIPE_Y1, CART_SWIPE_X2, CART_SWIPE_Y2)
    time.sleep(0.6)
    while True:
        matches = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, CART_DELETE_SELECTOR)
        if not matches:
            break
        try:
            matches[0].click()
            deleted += 1
            time.sleep(0.6)
        except Exception:
            break
    return deleted


def cart_back_to_cart(driver):
    """Unchanged from your script (back_to_cart), renamed to avoid clashing
    with runner.py's existing tap-based back helpers."""
    try:
        driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, CART_BACK_SELECTOR).click()
    except Exception:
        driver.back()
    time.sleep(1.2)


def clear_entire_cart(driver):
    """Same per-category loop as your script: tap tile -> swipe -> delete
    until empty -> back -> repeat until no more category rows."""
    brand_count = 0
    option_count = 0

    while True:
        time.sleep(0.5)
        cat_row = get_first_category_row(driver)
        if cat_row is None:
            break

        brand_count += 1
        try:
            cat_row.click()
            time.sleep(2)
        except Exception as e:
            raise RuntimeError(f"Could not tap category #{brand_count}: {e}")

        option_count += delete_all_options_in_category(driver)
        cart_back_to_cart(driver)

    # Best-effort extra back tap (el7 in your trace) — unchanged, still
    # can't crash the run if it's not present.
    try:
        driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, CART_EXTRA_BACK_SELECTOR).click()
        time.sleep(0.8)
    except Exception:
        pass

    if brand_count == 0:
        # Added — see note at top of file. Delete this block to restore
        # your original "silent pass on empty cart" behavior.
        raise RuntimeError("No category rows found — cart was already empty")

    return brand_count, option_count


def step_clear_cart(driver):
    clear_entire_cart(driver)


PRODUCT_DELETION = [
    step_open_app, step_scan_qr, step_picker_open, step_tap_photo,
    step_done_picker, step_return_app, step_tap_login, step_wait_home,
    step_cart_tab, step_clear_cart, step_save,
    step_signature, step_submit, step_wait_order, step_logout,
]




# TimeoutException is not imported at the top of runner.py yet.
from selenium.common.exceptions import TimeoutException

# ---------- Search functionality: config ----------

SEARCH_TERM = "bfk_hexon"
SEARCH_UPPER = SEARCH_TERM.upper()          # "BFK_HEXON" — matches the result row

ROUND_1_VALUE = "1"
ROUND_2_VALUE = "2"

SF_LOGOUT_TIMEOUT = 30      # hard cap — give up after this many seconds
SF_LOGOUT_POLL = 0.5        # how often to check whether Logout has appeared
SF_LOGOUT_COORD = None      # last-resort coordinate tap, e.g. (540, 1850); None = skip

# Coordinate fallbacks (1080-wide device) — only used if every locator fails
SEARCH_BOX_HOME_XY = (540, 472)    # search bar on the home screen  [42,396][1038,549]
SEARCH_BOX_BOYS_XY = (540, 362)    # search bar on the Boys screen  [21,286][1059,438]
SEARCH_RESULT_XY = (540, 500)      # first result row               [0,415][1080,585]
SF_HOME_BTN_XY = (540, 2221)       # floating Home button on the product screen
SF_NAV_CATALOGUE_XY = (888, 2219)
SF_NAV_CART_XY = (935, 2219)
SF_TICK_KEY_XY = (248, 2360)       # keyboard dismiss tick
SF_PLUS_BTN_XY = (887, 1919)       # "+" button fallback
SF_ADD_TO_CART_XY = (679, 2360)    # "Add to cart" fallback


# ---------- Search functionality: generic helpers ----------

def sf_tap_absolute(driver, x, y):
    """
    Tap at exact pixel coordinate.
    Do not reintroduce mobile: clickGesture as an unguarded call — BrowserStack's driver doesn't support it.

    Your local script used `mobile: clickGesture`. BrowserStack's UiAutomator2
    driver does NOT expose clickGesture (it offers dragGesture, longClickGesture,
    doubleClickGesture, swipeGesture... but no plain clickGesture), so that call
    raises UnknownMethodException on every device. W3C pointer actions are
    universally supported and are what runner.py's own `tap_xy` already uses for
    the two working test cases. clickGesture is still attempted first, so the
    behaviour is identical anywhere it IS available (e.g. a local emulator).
    """
    try:
        driver.execute_script("mobile: clickGesture", {"x": int(x), "y": int(y)})
        return int(x), int(y)
    except Exception:
        pass
    finger = PointerInput(interaction.POINTER_TOUCH, "finger")
    actions = ActionBuilder(driver, mouse=finger)
    actions.pointer_action.move_to_location(int(x), int(y))
    actions.pointer_action.pointer_down()
    actions.pointer_action.pause(0.1)
    actions.pointer_action.pointer_up()
    actions.perform()
    return int(x), int(y)


def sf_tap_element_center(driver, el):
    """Tap the centre of an element — works even when clickable=false."""
    loc, size = el.location, el.size
    x = int(loc["x"] + size["width"] / 2)
    y = int(loc["y"] + size["height"] / 2)
    sf_tap_absolute(driver, x, y)
    return x, y


def sf_find_any(driver, locators, timeout=6, poll=0.2):
    """
    Poll ALL locators in a round-robin until one resolves, sharing a single
    timeout budget, so a dead fallback costs ~50ms instead of a full timeout.
    Returns (element, locator) or (None, None).
    """
    end = time.time() + timeout
    while True:
        for by, val in locators:
            try:
                els = driver.find_elements(by, val)
            except Exception:
                continue
            for el in els:
                try:
                    if el.is_displayed() and el.is_enabled():
                        return el, (by, val)
                except Exception:
                    continue
        if time.time() >= end:
            return None, None
        time.sleep(poll)


def sf_click_first(driver, locators, timeout=6, label=""):
    """Click the first locator that resolves. Returns the locator used, or None."""
    start = time.time()
    el, used = sf_find_any(driver, locators, timeout=timeout)
    if el is None:
        return None
    try:
        el.click()
    except Exception:
        sf_tap_element_center(driver, el)   # non-clickable node — tap its centre
    if label:
        print(f"     [ok] {label} found in {time.time() - start:.1f}s "
              f"via: {str(used[1])[:55]}")
    return used


# ---------- Search functionality: search helpers ----------

def sf_type_text(driver, text):
    """
    Type into the focused field.

    Replaces the local script's `adb shell input text`, which cannot reach a
    BrowserStack device from the runner host. Order of attempts:
      1. the focused EditText (set_value, then send_keys)
      2. `mobile: shell` running `input text` — this is the exact same
         `input text` command your adb call ran, just routed through the
         Appium driver instead of a local subprocess. BrowserStack's driver
         DOES expose `shell` (it's first in the supported-command list, and
         runner.py's existing scan_media() already uses it).
      3. mobile: type (UiAutomator2 driver command)
    """
    boxes = []
    try:
        boxes = driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
    except Exception:
        pass

    for box in boxes:
        for writer in ("set_value", "send_keys"):
            try:
                if writer == "set_value":
                    box.set_value(text)
                else:
                    box.send_keys(text)
                try:
                    current = (box.get_attribute("text") or box.text or "").strip()
                except Exception:
                    current = text
                if text.lower() in current.lower():
                    print(f"     [ok] Typed '{text}' via EditText.{writer}")
                    return True
            except Exception:
                continue

    safe = text.replace(" ", "%s")
    try:
        driver.execute_script("mobile: shell",
                              {"command": "input", "args": ["text", safe]})
        print(f"     [ok] Typed '{text}' via mobile: shell input text")
        return True
    except Exception:
        pass

    try:
        driver.execute_script("mobile: type", {"text": text})
        print(f"     [ok] Typed '{text}' via mobile: type")
        return True
    except Exception:
        pass

    raise RuntimeError(f"Could not type '{text}' into the search box")


def sf_open_search_box(driver, coord_fallback, extra_locators=()):
    """
    Tap the 'Search by product code' bar.
    The element reports clickable=false on the home screen, so if a normal
    .click() doesn't take, tap its centre by gesture instead.
    """
    locators = [
        (AppiumBy.ACCESSIBILITY_ID, "Search by product code"),
        (AppiumBy.XPATH,
         '//android.widget.ImageView[@content-desc="Search by product code"]'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().description("Search by product code")'),
    ] + list(extra_locators)

    start = time.time()
    el, used = sf_find_any(driver, locators, timeout=6)
    if el is not None:
        x, y = sf_tap_element_center(driver, el)
        print(f"     [ok] Search bar tapped at ({x},{y}) in {time.time() - start:.1f}s "
              f"via: {str(used[1])[:55]}")
        return True

    print(f"     [warn] All locators failed — tapping search bar at {coord_fallback}")
    sf_tap_absolute(driver, *coord_fallback)
    return False


def sf_wait_for_keyboard(driver, timeout=6):
    """Wait until an EditText is focused / the keyboard is up."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if driver.is_keyboard_shown():
                return True
        except Exception:
            pass
        try:
            if driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText"):
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def sf_tap_search_result(driver):
    """Tap the first result row returned for SEARCH_TERM."""
    locators = [
        (AppiumBy.ANDROID_UIAUTOMATOR,
         f'new UiSelector().className("android.widget.ImageView")'
         f'.descriptionContains("{SEARCH_UPPER}")'),
        (AppiumBy.XPATH,
         f'//android.widget.ImageView[contains(@content-desc,"{SEARCH_UPPER}")]'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         f'new UiSelector().descriptionContains("{SEARCH_UPPER}")'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.ImageView").instance(0)'),
    ]
    start = time.time()
    el, used = sf_find_any(driver, locators, timeout=8)
    if el is not None:
        sf_tap_element_center(driver, el)
        print(f"     [ok] Result row tapped in {time.time() - start:.1f}s "
              f"via: {str(used[1])[:55]}")
        return True

    print(f"     [warn] Result locators failed — tapping {SEARCH_RESULT_XY}")
    sf_tap_absolute(driver, *SEARCH_RESULT_XY)
    return False


def sf_search_product(driver, coord_fallback, extra_locators=()):
    """Full search sub-flow: open search bar -> type -> tap first result."""
    sf_open_search_box(driver, coord_fallback, extra_locators)
    time.sleep(1.0)

    if not sf_wait_for_keyboard(driver):
        print("     [warn] Keyboard not detected — typing anyway")

    sf_type_text(driver, SEARCH_TERM)
    time.sleep(1.5)          # let the result list populate
    sf_tap_search_result(driver)
    time.sleep(1.2)


# ---------- Search functionality: booking helpers ----------

def sf_fill_sizes(driver, value):
    """Fill EVERY size EditText with `value`, then dismiss the keyboard."""
    try:
        size_inputs = WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located(
                (AppiumBy.CLASS_NAME, "android.widget.EditText")
            )
        )
    except Exception:
        size_inputs = []

    entered = 0
    for inp in size_inputs:
        try:
            inp.click()
            inp.clear()
            inp.set_value(value)
            entered += 1
            time.sleep(0.1)
        except Exception:
            try:
                inp.click()
                inp.clear()
                inp.send_keys(value)
                entered += 1
                time.sleep(0.1)
            except Exception:
                pass

    time.sleep(0.2)

    tick_locators = [
        (AppiumBy.XPATH, '//*[@content-desc="Dismiss"]'),
        (AppiumBy.ACCESSIBILITY_ID, "Dismiss"),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().description("Dismiss")'),
        (AppiumBy.XPATH, '//android.view.View[@content-desc="Dismiss"]'),
    ]
    if sf_click_first(driver, tick_locators, timeout=2):
        print("     [ok] Keyboard dismissed via Dismiss button")
    else:
        print(f"     [warn] Dismiss button not found — tapping tick key at {SF_TICK_KEY_XY}")
        sf_tap_absolute(driver, *SF_TICK_KEY_XY)

    time.sleep(0.3)
    return entered


def sf_tap_plus(driver):
    plus_locators = [
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.Button").text("+")'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.Button").instance(1)'),
        (AppiumBy.XPATH,
         '(//android.view.View[@content-desc="0"])[1]/android.widget.Button[2]'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.Button").instance(0)'),
    ]
    if not sf_click_first(driver, plus_locators, timeout=2, label="'+'"):
        print(f"     [warn] All locators failed — tapping + at {SF_PLUS_BTN_XY}")
        sf_tap_absolute(driver, *SF_PLUS_BTN_XY)
    time.sleep(0.5)


def sf_tap_add_to_cart(driver):
    cart_locators = [
        (AppiumBy.XPATH, '//android.widget.ImageView[@content-desc="Add to cart"]'),
        (AppiumBy.ACCESSIBILITY_ID, "Add to cart"),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().description("Add to cart")'),
    ]
    if not sf_click_first(driver, cart_locators, timeout=8, label="'Add to cart'"):
        print(f"     [warn] All locators failed — tapping Add to cart at {SF_ADD_TO_CART_XY}")
        sf_tap_absolute(driver, *SF_ADD_TO_CART_XY)
    time.sleep(0.8)


def sf_tap_home_button(driver):
    """
    Tap the floating Home button shown on the product screen after 'Add to cart'.
    This is an android.widget.Button (NOT the nav_home ImageView) —
    confirmed at bounds [466,2148][614,2295].
    """
    home_locators = [
        (AppiumBy.XPATH,
         "//android.widget.FrameLayout[@resource-id='android:id/content']"
         "/android.widget.FrameLayout/android.view.View/android.view.View"
         "/android.view.View/android.view.View/android.widget.Button"),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.Button").instance(1)'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.Button").instance(0)'),
        (AppiumBy.ACCESSIBILITY_ID, "Home"),
        (AppiumBy.ACCESSIBILITY_ID, "Home Tab"),
    ]
    if not sf_click_first(driver, home_locators, timeout=5, label="Home button"):
        print(f"     [warn] All locators failed — tapping Home at {SF_HOME_BTN_XY}")
        sf_tap_absolute(driver, *SF_HOME_BTN_XY)
    time.sleep(0.8)


def sf_tap_nav(driver, res_id, acc_names, coord, label):
    """Tap a bottom-nav icon by resource-id, with accessibility-id + coordinate fallbacks."""
    locators = [
        (AppiumBy.ID, res_id),
        (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().resourceId("{res_id}")'),
        (AppiumBy.XPATH, f'//android.widget.ImageView[@resource-id="{res_id}"]'),
    ] + [(AppiumBy.ACCESSIBILITY_ID, n) for n in acc_names]

    if not sf_click_first(driver, locators, timeout=5, label=label):
        print(f"     [warn] All locators failed — tapping {label} at {coord}")
        sf_tap_absolute(driver, *coord)
    time.sleep(0.9)


def sf_smart_logout(driver, timeout=SF_LOGOUT_TIMEOUT, poll=SF_LOGOUT_POLL):
    """
    Poll for the Logout button and click it THE MOMENT it appears, instead of
    blindly sleeping 30s after Submit. Returns elapsed seconds; raises if it
    never shows.
    """
    locators = [
        (AppiumBy.ACCESSIBILITY_ID, "Logout"),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Logout")'),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Logout")'),
    ]
    start = time.time()
    next_tick = 5.0

    print(f"     [..] Polling for Logout every {poll}s (max {timeout}s)...")
    while (time.time() - start) < timeout:
        for by, val in locators:
            try:
                el = driver.find_element(by, val)
                if el.is_displayed():
                    el.click()
                    elapsed = time.time() - start
                    print(f"     [ok] Logout appeared after {elapsed:.1f}s — clicked")
                    return elapsed
            except Exception:
                continue

        elapsed = time.time() - start
        if elapsed >= next_tick:
            print(f"        ...still saving ({elapsed:.0f}s elapsed)")
            next_tick += 5.0
        time.sleep(poll)

    if SF_LOGOUT_COORD:
        print(f"     [warn] Logout not found in {timeout}s — tapping {SF_LOGOUT_COORD}")
        sf_tap_absolute(driver, *SF_LOGOUT_COORD)
        return time.time() - start

    raise TimeoutException(
        f"Logout button did not appear within {timeout}s "
        f"(set SF_LOGOUT_COORD for a coordinate fallback)"
    )


# ---------- Search functionality: steps ----------
# Steps 1-8 (login) are the existing hardened runner.py steps, reused below in
# the SEARCH_FUNCTIONALITY list. These are steps 9-25.

def step_sf_search_home(driver):
    """Your Step 08 — search from the home screen."""
    sf_search_product(driver, SEARCH_BOX_HOME_XY)


def step_sf_sizes_round1(driver):
    """Your Step 09 — all size boxes = 1."""
    entered = sf_fill_sizes(driver, ROUND_1_VALUE)
    if entered == 0:
        raise RuntimeError("No EditText size fields found (round 1)")
    print(f"     [ok] Round 1: {entered} boxes set to '{ROUND_1_VALUE}'")


def step_sf_plus_round1(driver):
    """Your Step 10 — non-fatal in the original script, kept non-fatal here."""
    try:
        sf_tap_plus(driver)
    except Exception as e:
        print(f"     [warn] '+' round 1 failed, continuing (non-fatal): {e}")


def step_sf_add_to_cart_round1(driver):
    """Your Step 11."""
    sf_tap_add_to_cart(driver)


def step_sf_home_after_round1(driver):
    """Your Step 12."""
    sf_tap_home_button(driver)


def step_sf_catalogue(driver):
    """Your Step 13."""
    sf_tap_nav(driver, "nav_catalogue", ["Catalogue", "Catalogue Tab"],
               SF_NAV_CATALOGUE_XY, "Catalogue")


def step_sf_brand_boys(driver):
    """Your Step 14 — the tile's content-desc may carry the option count too."""
    boys_locators = [
        (AppiumBy.ACCESSIBILITY_ID, "Boys"),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionStartsWith("Boys")'),
        (AppiumBy.XPATH, '//android.view.View[starts-with(@content-desc,"Boys")]'),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Boys")'),
    ]
    if not sf_click_first(driver, boys_locators, timeout=6, label="'Boys' tile"):
        raise RuntimeError("'Boys' tile not found by any locator")
    time.sleep(1.0)


def step_sf_search_boys(driver):
    """Your Step 15 — the Boys-screen search bar exposes no content-desc."""
    sf_search_product(
        driver,
        SEARCH_BOX_BOYS_XY,
        extra_locators=[
            (AppiumBy.XPATH,
             '//android.widget.FrameLayout[@resource-id="android:id/content"]'
             '/android.widget.FrameLayout/android.view.View/android.view.View'
             '/android.view.View/android.view.View/android.widget.ImageView'),
            (AppiumBy.ANDROID_UIAUTOMATOR,
             'new UiSelector().className("android.widget.ImageView").instance(0)'),
        ],
    )


def step_sf_sizes_round2(driver):
    """Your Step 16 — all size boxes = 2."""
    entered = sf_fill_sizes(driver, ROUND_2_VALUE)
    if entered == 0:
        raise RuntimeError("No EditText size fields found (round 2)")
    print(f"     [ok] Round 2: {entered} boxes set to '{ROUND_2_VALUE}'")


def step_sf_plus_round2(driver):
    """Your Step 17 — non-fatal in the original script, kept non-fatal here."""
    try:
        sf_tap_plus(driver)
    except Exception as e:
        print(f"     [warn] '+' round 2 failed, continuing (non-fatal): {e}")


def step_sf_add_to_cart_round2(driver):
    """Your Step 18."""
    sf_tap_add_to_cart(driver)


def step_sf_home_after_round2(driver):
    """Your Step 19."""
    sf_tap_home_button(driver)


def step_sf_cart_tab(driver):
    """Your Step 20."""
    sf_tap_nav(driver, "nav_cart", ["Cart", "Cart Tab"], SF_NAV_CART_XY, "Cart")


def step_sf_save(driver):
    """Your Step 21."""
    WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, "SAVE"))
    ).click()
    time.sleep(0.8)


def step_sf_signature(driver):
    """Your Step 22 — non-fatal in the original script, kept non-fatal here.
    Uses runner.py's draw_signature(), which is identical to yours."""
    try:
        draw_signature(driver)
    except Exception as e:
        print(f"     [warn] Signature failed, continuing (non-fatal): {e}")


def step_sf_submit(driver):
    """Your Step 23."""
    WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, "Submit"))
    ).click()


def step_sf_logout(driver):
    """Your Step 24 — smart logout, polls instead of sleeping 30s."""
    elapsed = sf_smart_logout(driver)
    time.sleep(0.8)
    print(f"     [ok] Logout tapped {elapsed:.1f}s after Submit")


SEARCH_FUNCTIONALITY = [
    # Login (reused hardened runner.py steps) — 8
    step_open_app, step_scan_qr, step_picker_open, step_tap_photo,
    step_done_picker, step_return_app, step_tap_login, step_wait_home,
    # Round 1 — 4
    step_sf_search_home, step_sf_sizes_round1, step_sf_plus_round1,
    step_sf_add_to_cart_round1,
    # Navigate back and search again — 4
    step_sf_home_after_round1, step_sf_catalogue, step_sf_brand_boys,
    step_sf_search_boys,
    # Round 2 — 4
    step_sf_sizes_round2, step_sf_plus_round2, step_sf_add_to_cart_round2,
    step_sf_home_after_round2,
    # Finalise — 5
    step_sf_cart_tab, step_sf_save, step_sf_signature, step_sf_submit,
    step_sf_logout,
]   # 25 steps


# ---------- Filter functionality: config ----------

CATEGORY_FILTER = "Denim"        # Category tab     — content-desc is "Denim \n55"
SUBCATEGORY_FILTER = "Gymindigo"  # Sub-Category tab — content-desc is "Gymindigo \n2"
PRODUCT_TO_BOOK = "BD_DARIANS"   # the result to open — content-desc is "BD_DARIANS\n2599"
FLT_SIZE_VALUE = "1"             # quantity typed into every size box

# Set True to FAIL the run when PRODUCT_TO_BOOK is missing from the filtered
# grid, instead of opening whatever tile came first. See note above.
FLT_STRICT_TARGET = False

# Coordinate fallbacks (1080-wide device) — only used if every locator fails
FLT_FILTER_BTN_XY = (1017, 181)    # filter icon on Boys   [954,118][1080,244]
FLT_DENIM_CB_XY = (710, 619)       # "Denim" checkbox      [425,546][996,693]
FLT_SUBCATEGORY_XY = (170, 632)    # Sub-Category tab      [0,551][341,714]
FLT_SUBCAT_CB_XY = None            # no coordinate fallback for the sub-category box
FLT_APPLY_FILTERS_XY = (844, 2215)  # Apply Filters button  [660,2145][1028,2285]
FLT_FIRST_PRODUCT_XY = (265, 790)  # 1st result tile       [0,459][530,1121]

# SF_HOME_BTN_XY, SF_NAV_CATALOGUE_XY, SF_NAV_CART_XY, SF_TICK_KEY_XY,
# SF_PLUS_BTN_XY and SF_ADD_TO_CART_XY are already defined by the search
# functionality block and are reused as-is.


# ---------- Filter functionality: filter-panel helpers ----------

def flt_tap_filter_icon(driver):
    """
    Top-right filter icon on the brand screen.
    An unlabelled android.widget.Button — the 2nd Button in the header
    (instance(1)), bounds [954,118][1080,244].
    """
    locators = [
        (AppiumBy.XPATH,
         '//android.widget.FrameLayout[@resource-id="android:id/content"]'
         '/android.widget.FrameLayout/android.view.View/android.view.View'
         '/android.view.View/android.view.View/android.view.View[1]'
         '/android.widget.Button[2]'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         'new UiSelector().className("android.widget.Button").instance(1)'),
        (AppiumBy.ACCESSIBILITY_ID, "Filters"),
    ]
    if not sf_click_first(driver, locators, timeout=6, label="Filter icon"):
        print(f"     [warn] All locators failed — tapping Filter at {FLT_FILTER_BTN_XY}")
        sf_tap_absolute(driver, *FLT_FILTER_BTN_XY)
    time.sleep(1.2)


def flt_swipe_list_up(driver, ratio=0.5):
    """
    Scroll the filter list. Flutter lists aren't UiScrollable, so swipe.
    swipeGesture IS supported by BrowserStack's driver; the W3C pointer
    fallback underneath covers anything that isn't.
    """
    s = driver.get_window_size()
    w, h = s["width"], s["height"]
    top, bottom = int(h * 0.30), int(h * 0.80)
    try:
        driver.execute_script("mobile: swipeGesture", {
            "left": int(w * 0.35), "top": top,
            "width": int(w * 0.6), "height": bottom - top,
            "direction": "up", "percent": ratio, "speed": 1200,
        })
    except Exception:
        touch = PointerInput(interaction.POINTER_TOUCH, "finger")
        a = ActionBuilder(driver, mouse=touch)
        a.pointer_action.move_to_location(int(w * 0.7), bottom)
        a.pointer_action.pointer_down()
        a.pointer_action.move_to_location(int(w * 0.7), top)
        a.pointer_action.release()
        a.perform()
    time.sleep(0.8)


def flt_tick_checkbox(driver, name, coord=None, max_scrolls=4):
    """
    Tick a filter checkbox by its label, scrolling the list if it's below the fold.

    The content-desc carries the count on a second line ("Denim \\n55",
    "Gymindigo \\n2"), so an exact match on the label alone misses — the
    prefix/contains locators cover both shapes and survive the count changing
    as stock moves.
    Returns the checkbox's 'checked' attribute afterwards, or None.
    """
    locators = [
        (AppiumBy.ANDROID_UIAUTOMATOR,
         f'new UiSelector().className("android.widget.CheckBox")'
         f'.descriptionStartsWith("{name}")'),
        (AppiumBy.XPATH,
         f'//android.widget.CheckBox[starts-with(@content-desc,"{name}")]'),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         f'new UiSelector().descriptionStartsWith("{name}")'),
    ]

    el, used = sf_find_any(driver, locators, timeout=4)
    scrolls = 0
    while el is None and scrolls < max_scrolls:
        scrolls += 1
        print(f"     [..] '{name}' not visible — scrolling the list ({scrolls}/{max_scrolls})")
        flt_swipe_list_up(driver)
        el, used = sf_find_any(driver, locators, timeout=2)

    if el is None:
        if coord:
            print(f"     [warn] '{name}' checkbox not found — tapping {coord}")
            sf_tap_absolute(driver, *coord)
            time.sleep(0.8)
            return None
        raise RuntimeError(f"'{name}' checkbox not found after {max_scrolls} scroll(s)")

    try:
        el.click()
    except Exception:
        sf_tap_element_center(driver, el)
    print(f"     [ok] '{name}' checkbox tapped via: {str(used[1])[:55]}")
    time.sleep(0.8)

    # Confirm it actually toggled rather than assuming the tap landed
    try:
        el2, _ = sf_find_any(driver, locators, timeout=3)
        state = el2.get_attribute("checked") if el2 is not None else None
        print(f"     [..] '{name}' checked = {state}")
        return state
    except Exception:
        return None


def flt_tap_subcategory_tab(driver):
    locators = [
        (AppiumBy.ACCESSIBILITY_ID, "Sub-Category"),
        (AppiumBy.XPATH, '//android.widget.Button[@content-desc="Sub-Category"]'),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().description("Sub-Category")'),
    ]
    if not sf_click_first(driver, locators, timeout=6, label="'Sub-Category' tab"):
        print(f"     [warn] All locators failed — tapping Sub-Category at {FLT_SUBCATEGORY_XY}")
        sf_tap_absolute(driver, *FLT_SUBCATEGORY_XY)
    time.sleep(1.0)


def flt_tap_apply_filters(driver):
    locators = [
        (AppiumBy.ACCESSIBILITY_ID, "Apply Filters"),
        (AppiumBy.XPATH, '//android.widget.Button[@content-desc="Apply Filters"]'),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().description("Apply Filters")'),
    ]
    if not sf_click_first(driver, locators, timeout=6, label="'Apply Filters'"):
        print(f"     [warn] All locators failed — tapping Apply Filters at {FLT_APPLY_FILTERS_XY}")
        sf_tap_absolute(driver, *FLT_APPLY_FILTERS_XY)
    time.sleep(1.5)


def flt_open_result(driver, target=PRODUCT_TO_BOOK):
    """
    Open `target` from the filtered grid.

    Can't use ImageView.instance(0) — that's the search bar. A product tile is
    an ImageView carrying a content-desc ("BD_DARIANS\\n2599"), so collect the
    labelled ones, take the one matching `target`, and fall back to the
    topmost-leftmost tile if the name isn't among them.
    Returns (result_count, name_opened).
    """
    end = time.time() + 8
    tiles = []
    while time.time() < end:
        tiles = []
        try:
            for el in driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.ImageView"):
                try:
                    desc = el.get_attribute("content-desc") or ""
                    if not desc.strip() or "search by product code" in desc.lower():
                        continue
                    if not el.is_displayed():
                        continue
                    loc = el.location
                    tiles.append((loc["y"], loc["x"], el, desc))
                except Exception:
                    continue
        except Exception:
            pass
        if tiles:
            break
        time.sleep(0.4)

    if not tiles:
        if FLT_STRICT_TARGET:
            raise RuntimeError(
                "No labelled product tile found after applying filters")
        print(f"     [warn] No labelled tile found — tapping {FLT_FIRST_PRODUCT_XY}")
        sf_tap_absolute(driver, *FLT_FIRST_PRODUCT_XY)
        time.sleep(1.5)
        return 0, "unknown"

    tiles.sort(key=lambda t: (t[0], t[1]))
    names = [t[3].splitlines()[0] if t[3].splitlines() else t[3] for t in tiles]
    print(f"     [..] {len(tiles)} result(s) after filtering: {names}")

    match = next((t for t in tiles if t[3].upper().startswith(target.upper())), None)
    if match is None:
        if FLT_STRICT_TARGET:
            raise RuntimeError(
                f"'{target}' not in the filtered results — got {names}. "
                f"The filter may not have applied.")
        match = tiles[0]
        print(f"     [warn] '{target}' not among the results — opening '{names[0]}' instead")

    _, _, el, desc = match
    sf_tap_element_center(driver, el)
    opened = desc.splitlines()[0] if desc.splitlines() else desc
    print(f"     [ok] Opened: {opened}")
    time.sleep(1.5)
    return len(tiles), opened


# ---------- Filter functionality: steps ----------
# Steps 1-8 (login), 9-10 (Catalogue, Boys) and 21-25 (cart, SAVE, signature,
# Submit, logout) reuse existing runner.py / sf_* steps — see the list below.
# These are the filter-specific ones.

def step_flt_filter_icon(driver):
    """Your Step 10."""
    flt_tap_filter_icon(driver)


def step_flt_tick_category(driver):
    """Your Step 11 — tick 'Denim' under Category."""
    state = flt_tick_checkbox(driver, CATEGORY_FILTER, FLT_DENIM_CB_XY)
    print(f"     [ok] Category '{CATEGORY_FILTER}' ticked (checked={state})")


def step_flt_subcategory_tab(driver):
    """Your Step 12."""
    flt_tap_subcategory_tab(driver)


def step_flt_tick_subcategory(driver):
    """Your Step 13 — tick 'Gymindigo' under Sub-Category.
    No coordinate fallback here by design, so a miss is a clear FAIL."""
    state = flt_tick_checkbox(driver, SUBCATEGORY_FILTER, FLT_SUBCAT_CB_XY)
    print(f"     [ok] Sub-Category '{SUBCATEGORY_FILTER}' ticked (checked={state})")


def step_flt_apply_filters(driver):
    """Your Step 14."""
    flt_tap_apply_filters(driver)


def step_flt_open_result(driver):
    """Your Step 15 — open the target product from the filtered grid."""
    count, name = flt_open_result(driver)
    print(f"     [ok] Result opened ({name}, {count} result(s) after filtering)")


def step_flt_sizes(driver):
    """Your Step 16 — every size box = 1."""
    entered = sf_fill_sizes(driver, FLT_SIZE_VALUE)
    if entered == 0:
        raise RuntimeError("No EditText size fields found")
    print(f"     [ok] {entered} boxes set to '{FLT_SIZE_VALUE}'")


def step_flt_plus(driver):
    """Your Step 17 — non-fatal in the original script, kept non-fatal here."""
    try:
        sf_tap_plus(driver)
    except Exception as e:
        print(f"     [warn] '+' failed, continuing (non-fatal): {e}")


def step_flt_add_to_cart(driver):
    """Your Step 18."""
    sf_tap_add_to_cart(driver)


def step_flt_home(driver):
    """Your Step 19."""
    sf_tap_home_button(driver)


FILTER_FUNCTIONALITY = [
    # Login (reused hardened runner.py steps) — 8
    step_open_app, step_scan_qr, step_picker_open, step_tap_photo,
    step_done_picker, step_return_app, step_tap_login, step_wait_home,
    # Catalogue -> Boys (reused from the search case) — 2
    step_sf_catalogue, step_sf_brand_boys,
    # Filter panel — 5
    step_flt_filter_icon, step_flt_tick_category, step_flt_subcategory_tab,
    step_flt_tick_subcategory, step_flt_apply_filters,
    # Book the filtered result — 5
    step_flt_open_result, step_flt_sizes, step_flt_plus,
    step_flt_add_to_cart, step_flt_home,
    # Finalise (reused from the search case) — 5
    step_sf_cart_tab, step_sf_save, step_sf_signature, step_sf_submit,
    step_sf_logout,
]   # 25 steps


# ---------- Catalogue order: config ----------

CAT_QTY_VALUE = "3"        # quantity typed into every size box
CAT_PRODUCT_INDEX = 1      # 0 = first card, 1 = second card in the category

# Set True to assert the cart is empty after re-login (see note above)
CAT_ASSERT_CART_EMPTY_AFTER_RELOGIN = False

CAT_CATALOGUE_TAB_SELECTOR = 'new UiSelector().resourceId("nav_catalogue")'
CAT_CART_TAB_SELECTOR = 'new UiSelector().resourceId("nav_cart")'
CAT_TAP_X, CAT_TAP_Y = 940, 2234     # centre of nav_cart on this device

CAT_CATEGORY_NAME = "Boys"
CAT_CO_ORDINATES_DESC = "Co-Ordinates"
CAT_ADD_TO_CART_DESC = "Add to cart"
CAT_YES_BUTTON_DESC = "Yes"
CAT_TILE_SELECTOR = 'new UiSelector().className("android.widget.ImageView")'
CAT_EDITTEXT_SELECTOR = 'new UiSelector().className("android.widget.EditText")'
CAT_BUTTON_SELECTOR = 'new UiSelector().className("android.widget.Button")'
CAT_EMPTY_CART_TEXT = "Your Cart is Empty"


# ---------- Catalogue order: generic helpers ----------

def cat_swipe_screen(driver, direction="up", ratio=0.6):
    """Scroll the screen. Flutter lists are not UiScrollable, so this uses
    a plain swipe gesture over the middle of the screen."""
    s = driver.get_window_size()
    w, h = s["width"], s["height"]
    x = int(w * 0.5)
    top, bottom = int(h * 0.25), int(h * 0.80)
    span = int((bottom - top) * ratio)
    if direction == "up":          # content moves up = see lower items
        y1, y2 = bottom, bottom - span
    else:
        y1, y2 = top, top + span
    try:
        driver.execute_script("mobile: swipeGesture", {
            "left": int(w * 0.1), "top": top,
            "width": int(w * 0.8), "height": bottom - top,
            "direction": direction, "percent": 0.75, "speed": 1200,
        })
    except Exception:
        touch = PointerInput(interaction.POINTER_TOUCH, "finger")
        a = ActionBuilder(driver, mouse=touch)
        a.pointer_action.move_to_location(x, y1)
        a.pointer_action.pointer_down()
        a.pointer_action.move_to_location(x, y2)
        a.pointer_action.release()
        a.perform()
    time.sleep(0.8)


def cat_find_scrolling(driver, by, value, max_swipes=8):
    """Find an element, scrolling down (then back up) if needed."""
    elems = driver.find_elements(by, value)
    if elems:
        return elems[0]
    for _ in range(max_swipes):
        cat_swipe_screen(driver, "up")
        elems = driver.find_elements(by, value)
        if elems:
            return elems[0]
    for _ in range(max_swipes):
        cat_swipe_screen(driver, "down")
        elems = driver.find_elements(by, value)
        if elems:
            return elems[0]
    return None


def cat_force_tap(driver, elem):
    """
    Tap an element even when it reports clickable=false. Flutter buttons
    usually respond to a coordinate tap, so that is tried first.

    clickGesture is attempted ahead of the W3C tap so local runs behave
    identically, but BrowserStack rejects it, so the coordinate tap below is
    what actually runs there — which is the order your docstring intended.
    """
    errors = []
    try:
        r = elem.rect
        cx = int(r["x"] + r["width"] / 2)
        cy = int(r["y"] + r["height"] / 2)
    except Exception as e:
        errors.append(f"rect: {e}")
        cx = cy = None

    if cx is not None:
        try:
            driver.execute_script("mobile: clickGesture", {"x": cx, "y": cy})
            return f"clickGesture ({cx},{cy})"
        except Exception as e:
            errors.append(f"clickGesture: {e}")
        try:
            touch = PointerInput(interaction.POINTER_TOUCH, "finger")
            a = ActionBuilder(driver, mouse=touch)
            a.pointer_action.move_to_location(cx, cy)
            a.pointer_action.pointer_down()
            a.pointer_action.pause(0.12)
            a.pointer_action.release()
            a.perform()
            return f"touch tap ({cx},{cy})"
        except Exception as e:
            errors.append(f"touch: {e}")

    try:
        elem.click()
        return "click"
    except Exception as e:
        errors.append(f"click: {e}")

    try:
        parent = elem.find_element(AppiumBy.XPATH, "./ancestor::*[@clickable='true'][1]")
        parent.click()
        return "parent click"
    except Exception as e:
        errors.append(f"parent: {e}")

    raise RuntimeError("; ".join(errors))


def cat_tap_with_retry(driver, by, value, label, attempts=6, settle=1.5):
    """Find (scrolling if needed) and tap, using every tap method available."""
    last_err = None
    for _ in range(attempts):
        try:
            elem = cat_find_scrolling(driver, by, value)
            if elem is None:
                last_err = "not found even after scrolling"
                time.sleep(1)
                continue
            how = cat_force_tap(driver, elem)
            print(f"     tapped {label} via {how}")
            time.sleep(settle)
            return
        except Exception as e:
            last_err = e
            time.sleep(1)
    raise RuntimeError(f"{label} not found or not tappable: {last_err}")


def cat_dismiss_keyboard(driver):
    """Close only the keyboard. Does NOT tap 'Dismiss': in Flutter that is the
    popup's background barrier, and tapping it closes the edit popup unsaved."""
    try:
        if driver.is_keyboard_shown():
            driver.hide_keyboard()
            time.sleep(0.8)
    except Exception:
        pass


def cat_wait_for_login_screen(driver, timeout=25):
    """After logout, wait for the QR login screen instead of a blind sleep."""
    el, _ = sf_find_any(
        driver, [(AppiumBy.ACCESSIBILITY_ID, "Scan QR from gallery")],
        timeout=timeout
    )
    if el is None:
        print("     [warn] Login screen not detected — continuing anyway")
        return False
    return True


# ---------- Catalogue order: product / quantity / ratio helpers ----------

def cat_get_product_cards(driver):
    """Product cards on screen, ordered topmost then leftmost.
    A card's content-desc is 'name<newline>price'; cards without a price are
    used only if no card on screen has one."""
    with_price, without_price = [], []
    for elem in driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, CAT_TILE_SELECTOR):
        try:
            desc = (elem.get_attribute("content-desc") or "").strip()
            if not desc:
                continue
            r = elem.rect
            if r["y"] < 200 or r["height"] < 100:
                continue                       # nav icons, headers
            last_line = desc.split("\n")[-1].replace(",", "").strip()
            if last_line.replace(".", "").isdigit():
                with_price.append((r["y"], r["x"], elem, desc))
            else:
                without_price.append((r["y"], r["x"], elem, desc))
        except Exception:
            continue
    cards = with_price or without_price
    cards.sort(key=lambda c: (c[0], c[1]))
    return cards


def cat_find_first_list_item(driver):
    """First row of a list screen: a clickable ImageView with a one-line name."""
    rows = []
    for elem in driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, CAT_TILE_SELECTOR):
        try:
            desc = (elem.get_attribute("content-desc") or "").strip()
            if not desc or "\n" in desc:
                continue
            if (elem.get_attribute("clickable") or "") != "true":
                continue
            r = elem.rect
            if r["y"] < 300 or r["height"] < 80:
                continue
            rows.append((r["y"], r["x"], elem, desc))
        except Exception:
            continue
    if not rows:
        return None, None
    rows.sort(key=lambda x: (x[0], x[1]))
    return rows[0][2], rows[0][3]


def cat_find_qty_elements(driver):
    """
    Return the raw size/quantity input elements.

    Tries CLASS_NAME first — the SAME location strategy `sf_fill_sizes` uses,
    which is demonstrably working on this app in the search and filter tests.
    The UiSelector form is kept as a fallback. These are different Appium
    strategies: CLASS_NAME is resolved by the driver against its page-source
    tree, while ANDROID_UIAUTOMATOR hands a UiSelector to UiAutomator on the
    device. On Flutter surfaces they do not always agree, and the failing
    tests were using only the second one.
    """
    for by, val in ((AppiumBy.CLASS_NAME, "android.widget.EditText"),
                    (AppiumBy.ANDROID_UIAUTOMATOR, CAT_EDITTEXT_SELECTOR),
                    (AppiumBy.XPATH, "//android.widget.EditText")):
        try:
            els = driver.find_elements(by, val)
        except Exception:
            continue
        if els:
            return els
    return []


def cat_describe_screen(driver, limit=30):
    """
    One-line summary of what IS on screen, for error messages when the
    expected inputs are not found. Turns "no quantity box" into "no quantity
    box, but here are the 30 nodes that ARE there", which is what tells you
    whether the boxes are a different class or behind a tab.
    """
    out = []
    try:
        for el in driver.find_elements(AppiumBy.XPATH, "//*"):
            try:
                cls = (el.get_attribute("className") or "").split(".")[-1]
                label = (el.get_attribute("content-desc") or
                         el.get_attribute("text") or "").strip()
            except Exception:
                continue
            if not cls:
                continue
            out.append(f"{cls}:{label[:18]}" if label else cls)
            if len(out) >= limit:
                break
    except Exception:
        pass
    return ", ".join(out) or "(could not read screen)"


def cat_get_qty_boxes(driver):
    """
    Quantity boxes as (hint, element), topmost then leftmost.

    A box is NEVER dropped because `.rect` failed. The old version did that
    inside a bare `except: continue`, so one failing rect lookup could empty
    the whole list and surface as "no quantity box on this screen" even though
    the elements had been found. The returned key is also guaranteed non-empty
    so callers never need to call `.rect` again themselves.
    """
    boxes = []
    for idx, f in enumerate(cat_find_qty_elements(driver)):
        try:
            r = f.rect
            y, x = r["y"], r["x"]
        except Exception:
            try:
                loc = f.location
                y, x = loc["y"], loc["x"]
            except Exception:
                y, x = idx, 0          # keep document order rather than drop it
        try:
            hint = (f.get_attribute("hint") or "").strip()
        except Exception:
            hint = ""
        boxes.append((y, x, hint or f"@{y},{x}", f))
    boxes.sort(key=lambda b: (b[0], b[1]))
    return [(b[2], b[3]) for b in boxes]


def cat_wait_for_qty_boxes(driver, timeout=20):
    """Wait for the size/quantity fields to render, then return them."""
    end = time.time() + timeout
    while time.time() < end:
        boxes = cat_get_qty_boxes(driver)
        if boxes:
            return boxes
        time.sleep(0.5)
    return []


def cat_fill_box(box, value):
    """Type a value into one box and confirm it stuck."""
    box.click()
    time.sleep(0.4)
    try:
        box.clear()
    except Exception:
        pass
    try:
        box.set_value(value)
    except Exception:
        box.send_keys(value)
    time.sleep(0.5)
    try:
        return (box.get_attribute("text") or "").strip() == value
    except Exception:
        # Flutter rebuilds a text field when it takes focus, which makes this
        # reference stale. The typing itself succeeded; a stale read-back is
        # not a failure. Previously this raised and the box was recorded as
        # failed.
        print("     [..] field rebuilt after typing — treating as entered")
        return True


def cat_find_ratio_buttons(driver):
    """The -/+ pair in the ratio row: leftmost is -, rightmost is +."""
    s = driver.get_window_size()
    row = []
    for btn in driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, CAT_BUTTON_SELECTOR):
        try:
            r = btn.rect
            if s["height"] * 0.55 < r["y"] < s["height"] * 0.88:
                row.append((r["x"], btn))
        except Exception:
            continue
    row.sort(key=lambda b: b[0])
    return [b[1] for b in row]


def cat_read_ratio_count(driver):
    """Read the counter that sits between - and +."""
    try:
        for elem in driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR,
                                         'new UiSelector().className("android.view.View")'):
            desc = (elem.get_attribute("content-desc") or "").strip()
            if desc.isdigit() and elem.find_elements(
                    AppiumBy.ANDROID_UIAUTOMATOR, CAT_BUTTON_SELECTOR):
                return desc
    except Exception:
        pass
    return None


def cat_confirm_yes_if_present(driver, timeout=3):
    """Tap 'Yes' if a confirmation popup appears. Returns True if it did."""
    end = time.time() + timeout
    while time.time() < end:
        elems = driver.find_elements(AppiumBy.ACCESSIBILITY_ID, CAT_YES_BUTTON_DESC)
        if elems:
            try:
                how = cat_force_tap(driver, elems[0])
                print(f"     tapped 'Yes' via {how}")
                time.sleep(1.5)
                return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


# ---------- Catalogue order: cart helpers ----------

def cat_cart_is_empty(driver):
    """True if the cart screen shows the 'Your Cart is Empty' message."""
    for val in [f'new UiSelector().descriptionContains("{CAT_EMPTY_CART_TEXT}")',
                f'new UiSelector().textContains("{CAT_EMPTY_CART_TEXT}")',
                'new UiSelector().descriptionContains("Cart is Empty")',
                'new UiSelector().textContains("Cart is Empty")']:
        try:
            if driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, val):
                return True
        except Exception:
            continue
    return False


def cat_read_total_qty(driver):
    """Read the 'Total Qty: N' figure at the top of the cart. None if absent."""
    for val in ['new UiSelector().textContains("Total Qty")',
                'new UiSelector().descriptionContains("Total Qty")']:
        try:
            for elem in driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, val):
                raw = (elem.get_attribute("text") or
                       elem.get_attribute("content-desc") or "")
                digits = "".join(ch for ch in raw.split(":")[-1] if ch.isdigit())
                if digits:
                    return int(digits)
        except Exception:
            continue
    return None


def cat_cart_screen_open(driver):
    """True if the cart screen is showing: the totals row, the SAVE button,
    or the empty-cart message."""
    try:
        if cat_read_total_qty(driver) is not None:
            return True
        if driver.find_elements(AppiumBy.ACCESSIBILITY_ID, "SAVE"):
            return True
        if cat_cart_is_empty(driver):
            return True
        for val in ['new UiSelector().textContains("Total Amt")',
                    'new UiSelector().descriptionContains("Total Amt")',
                    'new UiSelector().textContains("Your Cart")',
                    'new UiSelector().descriptionContains("Your Cart")']:
            if driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, val):
                return True
    except Exception:
        pass
    return False


def cat_find_last_nav_button(driver):
    """The last (rightmost) icon in the bottom navigation bar — the Cart."""
    s_ = driver.get_window_size()
    row = []
    for val in ['new UiSelector().className("android.widget.ImageView")',
                'new UiSelector().className("android.widget.Button")']:
        try:
            for elem in driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, val):
                r = elem.rect
                cy = r["y"] + r["height"] / 2
                if cy > s_["height"] * 0.88 and r["height"] < s_["height"] * 0.12:
                    row.append((r["x"], elem))
        except Exception:
            continue
    if not row:
        return None
    row.sort(key=lambda b: b[0])
    return row[-1][1]            # rightmost = Cart


def cat_open_cart_tab(driver):
    """Open the Cart tab. Taps nav_cart with force_tap; if that is not found,
    taps the last icon in the bottom nav bar, then the nav bar coordinate as a
    final fallback."""
    locators = [
        (AppiumBy.ANDROID_UIAUTOMATOR, CAT_CART_TAB_SELECTOR),
        (AppiumBy.ID, "nav_cart"),
        (AppiumBy.XPATH, '//android.widget.ImageView[@resource-id="nav_cart"]'),
        (AppiumBy.ACCESSIBILITY_ID, "Cart"),
    ]
    for attempt in range(3):
        tab, how_found = None, ""
        for by, val in locators:
            try:
                elems = driver.find_elements(by, val)
                if elems:
                    tab, how_found = elems[0], str(val)[:45]
                    break
            except Exception:
                continue

        if tab is None:
            tab = cat_find_last_nav_button(driver)   # last button on the right
            how_found = "last icon in the bottom nav"

        if attempt > 0:                     # element tap did not work, use coordinates
            print(f"     tapping Cart at ({CAT_TAP_X},{CAT_TAP_Y})")
            sf_tap_absolute(driver, CAT_TAP_X, CAT_TAP_Y)
        elif tab is not None:
            try:
                how = cat_force_tap(driver, tab)
                print(f"     tapped Cart ({how_found}) via {how}")
            except Exception as e:
                print(f"     Cart tap failed: {e}")
        else:
            print(f"     no nav icon found - tapping ({CAT_TAP_X},{CAT_TAP_Y})")
            sf_tap_absolute(driver, CAT_TAP_X, CAT_TAP_Y)

        time.sleep(2)
        if cat_cart_screen_open(driver):
            return
        print(f"     cart screen not open yet (attempt {attempt + 1}/3)")

    raise RuntimeError("Cart tab tapped, but the cart screen did not open")


# ---------- Catalogue order: steps ----------
# Steps 1-8 and 28-35 (both logins) reuse the hardened runner.py steps.

def step_cat_catalogue(driver):
    """Your Step 09."""
    cat_tap_with_retry(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                       CAT_CATALOGUE_TAB_SELECTOR, "Catalogue tab (nav_catalogue)")


def step_cat_category(driver):
    """Your Step 10 — options count changes, so match the name only."""
    cat = cat_find_scrolling(
        driver, AppiumBy.ANDROID_UIAUTOMATOR,
        f'new UiSelector().descriptionStartsWith("{CAT_CATEGORY_NAME}")', max_swipes=6)
    if cat is None:
        cat = cat_find_scrolling(
            driver, AppiumBy.XPATH,
            f'//*[starts-with(@content-desc,"{CAT_CATEGORY_NAME}")]', max_swipes=4)
    if cat is None:
        raise RuntimeError(
            f"'{CAT_CATEGORY_NAME}' category not found even after scrolling")
    desc = (cat.get_attribute("content-desc") or "").replace("\n", " ").strip()
    how = cat_force_tap(driver, cat)
    print(f"     tapped category '{desc}' via {how}")
    time.sleep(2)


def step_cat_product(driver):
    """Your Step 11 — 2nd product in the category, no scrolling."""
    time.sleep(1)
    cards = cat_get_product_cards(driver)   # pick from what is on screen
    if len(cards) <= CAT_PRODUCT_INDEX:
        raise RuntimeError(f"only {len(cards)} product(s) visible on screen, "
                           f"need at least {CAT_PRODUCT_INDEX + 1}")
    prod = cards[CAT_PRODUCT_INDEX][2]
    desc = cards[CAT_PRODUCT_INDEX][3].replace("\n", " ").strip()
    how = cat_force_tap(driver, prod)
    print(f"     tapped product #{CAT_PRODUCT_INDEX + 1} '{desc}' via {how}")
    time.sleep(2)


def step_cat_quantity(driver):
    """
    Your Step 12 — quantity in every size box.

    heartbeat() per round is the ONE addition: this loop can run 80-90s on a
    product with many sizes, and runner-next.ts reaps a run after 90s without
    a database update. The typing logic below is unchanged.
    """
    run_id = RUNNER_STATUS.get("last_job_id")
    if not cat_wait_for_qty_boxes(driver):
        raise RuntimeError(
            "no quantity box found on this screen (waited 20s). On screen: "
            + cat_describe_screen(driver))
    filled, failed = {}, []
    for rnd in range(8):
        heartbeat(driver, run_id,
                  f"Entering quantities (pass {rnd + 1}/8, {len(filled)} boxes done)")
        progress = False
        for hint, box in cat_get_qty_boxes(driver):
            key = hint or f"box@{box.rect['y']}"
            if key in filled:
                continue
            try:
                if cat_fill_box(box, CAT_QTY_VALUE):
                    filled[key] = CAT_QTY_VALUE
                    progress = True
                    print(f"     size {key} -> {CAT_QTY_VALUE}")
                else:
                    failed.append(key)
            except Exception as e:
                failed.append(f"{key} ({e})")
        cat_dismiss_keyboard(driver)
        visible = [h for h, _ in cat_get_qty_boxes(driver)]
        if not progress and visible and all(h in filled for h in visible):
            break
        cat_swipe_screen(driver, "up", ratio=0.4)

    if not filled:
        raise RuntimeError("no quantity box found on this screen")
    if failed:
        raise RuntimeError(f"{len(filled)} boxes filled, these failed: {failed}")
    print(f"     [ok] Quantity '{CAT_QTY_VALUE}' entered in {len(filled)} size boxes")


def step_cat_plus(driver):
    """Your Step 13 — tap + to add the filled ratio."""
    buttons = cat_find_ratio_buttons(driver)
    for _ in range(4):
        if buttons:
            break
        cat_swipe_screen(driver, "up", ratio=0.4)
        buttons = cat_find_ratio_buttons(driver)
    if not buttons:
        raise RuntimeError("'+' button not found")
    before = cat_read_ratio_count(driver)
    how = cat_force_tap(driver, buttons[-1])        # rightmost = +
    print(f"     tapped '+' via {how}")
    time.sleep(1.5)
    after = cat_read_ratio_count(driver)
    print(f"     [ok] '+' tapped (ratio {before} -> {after})")


def step_cat_add_to_cart_1(driver):
    """Your Step 14 — Add to cart (1st product)."""
    btn = cat_find_scrolling(driver, AppiumBy.ACCESSIBILITY_ID,
                             CAT_ADD_TO_CART_DESC, max_swipes=4)
    if btn is None:
        raise RuntimeError("'Add to cart' not found even after scrolling")
    how = cat_force_tap(driver, btn)
    print(f"     tapped 'Add to cart' via {how}")
    time.sleep(2)
    cat_confirm_yes_if_present(driver)      # popup only appears sometimes


def step_cat_bottom_button(driver):
    """Your Step 15 — bottom-centre button."""
    target = None
    for _ in range(5):
        s = driver.get_window_size()
        cands = []
        for btn in driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, CAT_BUTTON_SELECTOR):
            try:
                r = btn.rect
                cx = r["x"] + r["width"] / 2
                if r["y"] > s["height"] * 0.85 and abs(cx - s["width"] / 2) < s["width"] * 0.2:
                    cands.append((r["y"], btn))
            except Exception:
                continue
        if cands:
            cands.sort(key=lambda c: c[0])
            target = cands[-1][1]
            break
        time.sleep(1)
    if target is None:
        raise RuntimeError("bottom-centre button not found")
    how = cat_force_tap(driver, target)
    print(f"     tapped bottom-centre button via {how}")
    time.sleep(2)


def step_cat_cart_tab(driver):
    """Your Steps 16 / 22 / 36 — open the Cart tab."""
    cat_open_cart_tab(driver)


def step_cat_co_ordinates(driver):
    """Your Step 17."""
    cat_tap_with_retry(driver, AppiumBy.ACCESSIBILITY_ID, CAT_CO_ORDINATES_DESC,
                       "'Co-Ordinates' option", settle=2)


def step_cat_list_item(driver):
    """Your Step 18 — first item in the Co-Ordinates list."""
    item, desc = cat_find_first_list_item(driver)
    for _ in range(4):
        if item is not None:
            break
        cat_swipe_screen(driver, "up", ratio=0.4)
        item, desc = cat_find_first_list_item(driver)
    if item is None:
        raise RuntimeError("no item found in the Co-Ordinates list")
    how = cat_force_tap(driver, item)
    print(f"     tapped list item '{desc}' via {how}")
    time.sleep(2)


def step_cat_minus(driver):
    """Your Step 19 — tap - to reduce the ratio."""
    buttons = cat_find_ratio_buttons(driver)
    for _ in range(4):
        if len(buttons) >= 2:
            break
        cat_swipe_screen(driver, "up", ratio=0.4)
        buttons = cat_find_ratio_buttons(driver)
    if len(buttons) < 2:
        raise RuntimeError(
            f"minus/plus pair not found (found {len(buttons)} button(s))")
    before = cat_read_ratio_count(driver)
    how = cat_force_tap(driver, buttons[0])         # leftmost = -
    print(f"     tapped '-' via {how}")
    time.sleep(1.5)
    after = cat_read_ratio_count(driver)
    print(f"     [ok] '-' tapped (ratio {before} -> {after})")


def step_cat_add_to_cart_2(driver):
    """Your Step 20 — Add to cart (2nd product)."""
    btn = cat_find_scrolling(driver, AppiumBy.ACCESSIBILITY_ID,
                             CAT_ADD_TO_CART_DESC, max_swipes=4)
    if btn is None:
        raise RuntimeError("'Add to cart' not found even after scrolling")
    how = cat_force_tap(driver, btn)
    print(f"     tapped 'Add to cart' via {how}")
    time.sleep(2)


def step_cat_confirm_yes(driver):
    """Your Step 21 — confirm with Yes. heartbeat() added: this loop can run
    ~20s and sits between two other slow steps."""
    run_id = RUNNER_STATUS.get("last_job_id")
    ok, tapped, last_err = False, False, "'Yes' button not found"
    for attempt in range(10):
        if attempt and attempt % 4 == 0:
            heartbeat(driver, run_id, f"Confirming order (attempt {attempt}/10)")
        elems = driver.find_elements(AppiumBy.ACCESSIBILITY_ID, CAT_YES_BUTTON_DESC)
        if not elems:
            if tapped:
                ok = True
                break
            time.sleep(1)
            continue
        try:
            how = cat_force_tap(driver, elems[0])
            print(f"     tapped 'Yes' via {how}")
            tapped = True
            time.sleep(2)
            if not driver.find_elements(AppiumBy.ACCESSIBILITY_ID, CAT_YES_BUTTON_DESC):
                ok = True
                break
            last_err = "tapped, but the popup is still open"
        except Exception as e:
            last_err = e
            time.sleep(1)
    if not ok:
        raise RuntimeError(last_err)


def step_cat_save(driver):
    """Your Step 23."""
    try:
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, "SAVE"))
        ).click()
    except Exception as e:
        raise RuntimeError(f"SAVE not clickable — cart empty? {e}")
    time.sleep(0.8)


def step_cat_signature(driver):
    """Your Step 24 — fatal in this script (unlike the search/filter cases)."""
    draw_signature(driver)


def step_cat_submit(driver):
    """Your Step 25."""
    WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, "Submit"))
    ).click()


def step_cat_order_saved(driver):
    """Your Step 26 — poll for Logout and click it the moment it renders."""
    elapsed = sf_smart_logout(driver)
    time.sleep(0.8)
    print(f"     [ok] Order saved (Logout appeared in {elapsed:.1f}s)")


def step_cat_login_screen(driver):
    """Your Step 27 — confirm logout actually returned to the login screen."""
    if not cat_wait_for_login_screen(driver):
        raise RuntimeError("Login screen did not appear after logout")


def step_cat_cart_after_relogin(driver):
    """
    Your Step 36 — open the Cart again after re-login.

    Set CAT_ASSERT_CART_EMPTY_AFTER_RELOGIN = True to also assert the cart is
    empty, which is what actually tests whether cart state survives a logout.
    Off by default so this matches your local runs.
    """
    cat_open_cart_tab(driver)
    if not CAT_ASSERT_CART_EMPTY_AFTER_RELOGIN:
        qty = cat_read_total_qty(driver)
        print(f"     [..] Cart after re-login: empty={cat_cart_is_empty(driver)}, "
              f"total qty={qty}")
        return
    if not cat_cart_is_empty(driver):
        qty = cat_read_total_qty(driver)
        raise RuntimeError(
            f"Cart is not empty after re-login (total qty={qty}) — the submitted "
            f"order did not clear it")


CATALOGUE_ORDER = [
    # Login (reused hardened runner.py steps) — 8
    step_open_app, step_scan_qr, step_picker_open, step_tap_photo,
    step_done_picker, step_return_app, step_tap_login, step_wait_home,
    # Catalogue -> product -> first booking — 7
    step_cat_catalogue, step_cat_category, step_cat_product, step_cat_quantity,
    step_cat_plus, step_cat_add_to_cart_1, step_cat_bottom_button,
    # Cart -> Co-Ordinates -> second booking — 6
    step_cat_cart_tab, step_cat_co_ordinates, step_cat_list_item,
    step_cat_minus, step_cat_add_to_cart_2, step_cat_confirm_yes,
    # Cart -> SAVE -> submit -> logout — 6
    step_cat_cart_tab, step_cat_save, step_cat_signature, step_cat_submit,
    step_cat_order_saved, step_cat_login_screen,
    # Re-login (same hardened steps again) — 8
    step_open_app, step_scan_qr, step_picker_open, step_tap_photo,
    step_done_picker, step_return_app, step_tap_login, step_wait_home,
    # Verify the cart — 1
    step_cat_cart_after_relogin,
]   # 36 steps


# ---------- Size edit: config ----------

SE_RATIO_VALUE = "5"       # ratio typed into every size field in the catalogue
SE_SIZE_NUMBER = "10"      # which size to edit in the Size Wise report
SE_SIZE_VALUE = "9"        # value typed into that size's field in the report
SE_BRAND_NAME = "Boys"     # brand to open in the catalogue
SE_OPTION_INDEX = 1        # 0 = first option, 1 = second option
SE_MAX_SIZE_EDITS = 10     # safety cap on the edit loop

SE_HOME_TAB_SELECTOR = 'new UiSelector().resourceId("nav_home")'
SE_REPORTS_TAB_SELECTOR = 'new UiSelector().resourceId("nav_reports")'
SE_BRAND_REPORT_DESC = "Brand Report"
SE_SIZE_WISE_CATEGORY_DESC = "Size Wise Report At Category"
SE_TILE_DESC = SE_SIZE_NUMBER
SE_EDIT_SIZE_DESC = f"Edit Size {SE_SIZE_NUMBER}"
SE_SECOND_CHECKBOX_SELECTOR = (
    'new UiSelector().className("android.widget.HorizontalScrollView")'
    '.childSelector(new UiSelector().className("android.widget.CheckBox").instance(1))'
)
SE_SAVE_CHANGES_SELECTOR = 'new UiSelector().descriptionContains("Save Changes")'

SE_EDIT_SIZE_LOCATORS = [
    (AppiumBy.ACCESSIBILITY_ID, SE_EDIT_SIZE_DESC),
    (AppiumBy.ANDROID_UIAUTOMATOR,
     f'new UiSelector().descriptionContains("{SE_EDIT_SIZE_DESC}")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Edit Size")'),
    (AppiumBy.XPATH, '//*[contains(@content-desc,"Edit Size")]'),
]

# Carries the booked option name from step 11 to step 28 — runner steps are
# separate calls, so a local variable would not survive between them.
SE_STATE: dict = {}


# ---------- Size edit: navigation helpers ----------

def se_find_top_left_back_button(driver):
    s = driver.get_window_size()
    for btn in driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, CAT_BUTTON_SELECTOR):
        try:
            r = btn.rect
            if r["x"] < s["width"] * 0.25 and r["y"] < s["height"] * 0.15:
                return btn
        except Exception:
            continue
    return None


def se_tap_back_button(driver):
    """Tap the top-left back button; falls back to system back."""
    for _ in range(5):
        btn = se_find_top_left_back_button(driver)
        if btn is not None:
            try:
                btn.click()
                time.sleep(1.5)
                return True
            except Exception:
                pass
        time.sleep(1)
    driver.back()
    time.sleep(1.5)
    return False


def se_get_card_list(driver):
    """Cards on screen (brands or options), topmost then leftmost.
    A card's content-desc is 'name<newline>extra'."""
    with_extra, plain = [], []
    elems = []
    for val in [CAT_TILE_SELECTOR,
                'new UiSelector().className("android.view.View")']:
        try:
            elems.extend(driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, val))
        except Exception:
            continue
        if with_extra or plain:
            break
    for elem in elems:
        try:
            desc = (elem.get_attribute("content-desc") or "").strip()
            if not desc:
                continue
            r = elem.rect
            if r["y"] < 200 or r["height"] < 100:
                continue                       # nav icons, headers
            (with_extra if "\n" in desc else plain).append((r["y"], r["x"], elem, desc))
        except Exception:
            continue
    cards = with_extra or plain
    cards.sort(key=lambda c: (c[0], c[1]))
    return cards


# ---------- Size edit: add-to-cart helpers ----------

def se_find_add_to_cart(driver):
    """The 'Add to cart' button: wide, short, low on the screen."""
    s_ = driver.get_window_size()
    cands = []
    for by, val in [
        (AppiumBy.ACCESSIBILITY_ID, CAT_ADD_TO_CART_DESC),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         f'new UiSelector().descriptionContains("{CAT_ADD_TO_CART_DESC}")'),
    ]:
        try:
            for elem in driver.find_elements(by, val):
                try:
                    r = elem.rect
                except Exception:
                    continue
                if r["height"] < 90 or r["height"] > 320:
                    continue
                if r["width"] < s_["width"] * 0.30:
                    continue
                if r["y"] < s_["height"] * 0.40:
                    continue
                cands.append((r["y"], elem))
        except Exception:
            continue
        if cands:
            break
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])
    return cands[0][1]


def se_tap_add_to_cart(driver):
    """Tap 'Add to cart' and clear the Yes popup if it appears."""
    btn = None
    for _ in range(3):
        btn = se_find_add_to_cart(driver)
        if btn is not None:
            break
        time.sleep(1)
    if btn is None:
        raise RuntimeError("'Add to cart' button not found")
    r = btn.rect
    how = cat_force_tap(driver, btn)
    print(f"     tapped 'Add to cart' at y={r['y']}..{r['y'] + r['height']} via {how}")
    time.sleep(2)
    cat_confirm_yes_if_present(driver)


# ---------- Size edit: report helpers ----------

def se_tap_report_option(driver, skip_text=None, prefer=None):
    """Tap the option name on a report page (left column). Headings, the
    summary line and the right-hand category column are skipped. When the
    booked option name is known it is tried first."""
    if prefer:
        key = prefer.split()[0] if prefer.split() else prefer
        for by, val in [
            (AppiumBy.ACCESSIBILITY_ID, prefer),
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().descriptionContains("{key}")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{key}")'),
        ]:
            try:
                elems = driver.find_elements(by, val)
            except Exception:
                continue
            for elem in elems:
                try:
                    r = elem.rect
                except Exception:
                    continue
                if r["height"] < 30:
                    continue
                text = ((elem.get_attribute("text") or "") + " " +
                        (elem.get_attribute("content-desc") or "")).strip()
                how = cat_force_tap(driver, elem)
                time.sleep(2)
                return (text or prefer), how

    s_ = driver.get_window_size()
    skip = ("detailed brand report", "brand report", "option name", "category",
            "option", "non-core", "core", "total", "save", SE_BRAND_NAME.lower())
    rows = []
    for val in ['new UiSelector().className("android.view.View")',
                'new UiSelector().className("android.widget.TextView")',
                'new UiSelector().className("android.widget.ImageView")']:
        try:
            for elem in driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, val):
                text = ((elem.get_attribute("text") or "") + " " +
                        (elem.get_attribute("content-desc") or "")).strip()
                if not text or len(text) > 60 or "|" in text:
                    continue
                low = text.lower().strip()
                if any(low == w or low.startswith(w) for w in skip):
                    continue
                if low.replace(",", "").replace(".", "").isdigit():
                    continue
                if skip_text and text.strip() == skip_text.strip():
                    continue
                r = elem.rect
                if r["y"] < s_["height"] * 0.12 or r["y"] > s_["height"] * 0.90:
                    continue
                if r["x"] > s_["width"] * 0.65:          # right column = Category
                    continue
                rows.append((r["y"], r["x"], elem, text))
        except Exception:
            continue
    if not rows:
        raise RuntimeError("no option found on the report page")
    rows.sort(key=lambda x: (x[0], x[1]))
    elem, text = rows[0][2], rows[0][3]
    how = cat_force_tap(driver, elem)
    time.sleep(2)
    return text, how


# ---------- Size edit: edit-view helpers ----------

def se_size_input_visible(driver):
    """True once the edit view is open. XPath cannot filter on @hint reliably,
    so any EditText on screen counts as the edit view being open."""
    try:
        return len(driver.find_elements(
            AppiumBy.ANDROID_UIAUTOMATOR, CAT_EDITTEXT_SELECTOR)) > 0
    except Exception:
        return False


def se_find_size_input(driver):
    """Return the size input box: the EditText whose hint matches, else the
    only EditText on screen, else the first one."""
    fields = driver.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, CAT_EDITTEXT_SELECTOR)
    if not fields:
        raise RuntimeError(f"size {SE_SIZE_NUMBER} input field not found")
    for f in fields:
        try:
            if (f.get_attribute("hint") or "").strip() == SE_TILE_DESC:
                return f
        except Exception:
            continue
    if len(fields) > 1:
        print(f"     note: {len(fields)} input fields on screen, using the first")
    return fields[0]


def se_find_save_changes(driver, scroll=True):
    """Return the 'Save Changes' button or None (tries several locators,
    scrolling if the button is below the fold)."""
    locators = [
        (AppiumBy.ANDROID_UIAUTOMATOR, SE_SAVE_CHANGES_SELECTOR),
        (AppiumBy.ACCESSIBILITY_ID, " Save Changes"),
        (AppiumBy.ACCESSIBILITY_ID, "Save Changes"),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Save Changes")'),
        (AppiumBy.XPATH,
         '//*[contains(@content-desc,"Save") or contains(@text,"Save Changes")]'),
    ]
    for by, val in locators:
        try:
            elems = driver.find_elements(by, val)
            if elems:
                return elems[0]
        except Exception:
            continue
    if scroll:
        for _ in range(4):
            cat_swipe_screen(driver, "up", ratio=0.4)
            for by, val in locators:
                try:
                    elems = driver.find_elements(by, val)
                    if elems:
                        return elems[0]
                except Exception:
                    continue
    return None


def se_select_checkbox_2(driver):
    """Select the 2nd checkbox (skips the tap if it is already checked)."""
    last_err = None
    for _ in range(10):
        try:
            box = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR,
                                      SE_SECOND_CHECKBOX_SELECTOR)
            if box.get_attribute("checked") != "true":
                box.click()
                time.sleep(1)
                box = driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR,
                                          SE_SECOND_CHECKBOX_SELECTOR)
            if box.get_attribute("checked") == "true":
                return
            last_err = "tapped, but checkbox still shows unchecked"
        except Exception as e:
            last_err = e
        time.sleep(1)
    raise RuntimeError(f"2nd checkbox could not be selected: {last_err}")


def se_find_edit_size_button(driver):
    for by, val in SE_EDIT_SIZE_LOCATORS:
        try:
            elems = driver.find_elements(by, val)
            if elems:
                return elems[0]
        except Exception:
            continue
    for _ in range(4):
        cat_swipe_screen(driver, "up", ratio=0.4)
        for by, val in SE_EDIT_SIZE_LOCATORS:
            try:
                elems = driver.find_elements(by, val)
                if elems:
                    return elems[0]
            except Exception:
                continue
    return None


def se_open_size_edit_view(driver):
    """Open the size edit view. Returns once an input field is on screen."""
    last_err = f"'{SE_EDIT_SIZE_DESC}' not found"
    for _ in range(6):
        try:
            if se_size_input_visible(driver):
                return
            elem = se_find_edit_size_button(driver)
            if elem is not None:
                how = cat_force_tap(driver, elem)
                print(f"     tapped '{SE_EDIT_SIZE_DESC}' via {how}")
                time.sleep(2)
                if se_size_input_visible(driver):
                    return
                last_err = f"tapped '{SE_EDIT_SIZE_DESC}', but no input field appeared"
            else:
                last_err = f"'{SE_EDIT_SIZE_DESC}' not found even after scrolling"
                time.sleep(1)
        except Exception as e:
            last_err = e
            time.sleep(1)
    raise RuntimeError(last_err)


def se_edit_one_size(driver, value):
    """Open the size edit view, type the value, close the keyboard and tap
    Save Changes. Returns True if a Save Changes button was tapped."""
    se_open_size_edit_view(driver)

    ok, last_err = False, None
    for _ in range(5):
        try:
            field = se_find_size_input(driver)
            field.click()
            time.sleep(0.4)
            try:
                field.clear()
            except Exception:
                pass
            try:
                field.set_value(value)
            except Exception:
                field.send_keys(value)
            time.sleep(0.6)
            actual = (se_find_size_input(driver).get_attribute("text") or "").strip()
            if actual == value:
                ok = True
                break
            last_err = f"field shows '{actual}', expected '{value}'"
        except Exception as e:
            last_err = e
        time.sleep(1)
    if not ok:
        raise RuntimeError(f"could not enter size {SE_SIZE_NUMBER} value: {last_err}")

    if se_find_save_changes(driver, scroll=False) is None:
        cat_dismiss_keyboard(driver)

    tapped, last_err = False, "'Save Changes' not found"
    for _ in range(8):
        btn = se_find_save_changes(driver)
        if btn is None:
            if tapped:
                return True                    # tapped and the popup closed
            time.sleep(1)
            continue
        try:
            how = cat_force_tap(driver, btn)
            print(f"     tapped 'Save Changes' via {how}")
            tapped = True
            time.sleep(2)
            if se_find_save_changes(driver, scroll=False) is None:
                return True
            last_err = "tapped, but 'Save Changes' is still on screen"
        except Exception as e:
            last_err = e
            time.sleep(1)
    raise RuntimeError(str(last_err))


# ---------- Size edit: steps ----------
# Steps 1-8 (login) reuse the hardened runner.py steps.

def step_se_catalogue(driver):
    """Your Step 09."""
    cat_tap_with_retry(driver, AppiumBy.ANDROID_UIAUTOMATOR,
                       CAT_CATALOGUE_TAB_SELECTOR, "Catalogue tab (nav_catalogue)")


def step_se_brand(driver):
    """Your Step 10 — the tile count changes, so match the name only."""
    brand = cat_find_scrolling(
        driver, AppiumBy.ANDROID_UIAUTOMATOR,
        f'new UiSelector().descriptionStartsWith("{SE_BRAND_NAME}")', max_swipes=6)
    if brand is None:
        brand = cat_find_scrolling(
            driver, AppiumBy.ANDROID_UIAUTOMATOR,
            f'new UiSelector().textStartsWith("{SE_BRAND_NAME}")', max_swipes=4)
    if brand is None:
        raise RuntimeError(f"'{SE_BRAND_NAME}' not found even after scrolling")
    brand_name = ((brand.get_attribute("content-desc") or "") or
                  (brand.get_attribute("text") or "")).replace("\n", " ").strip()
    how = cat_force_tap(driver, brand)
    print(f"     tapped brand '{brand_name}' via {how}")
    time.sleep(2)


def step_se_option(driver):
    """Your Step 11 — 2nd option, no scrolling. Records the option name in
    SE_STATE so step 28 can prefer it."""
    time.sleep(1)
    options = se_get_card_list(driver)
    if len(options) <= SE_OPTION_INDEX:
        raise RuntimeError(f"only {len(options)} option(s) visible, "
                           f"need at least {SE_OPTION_INDEX + 1}")
    option_name = options[SE_OPTION_INDEX][3].replace("\n", " ").strip()
    SE_STATE["option_name"] = option_name
    how = cat_force_tap(driver, options[SE_OPTION_INDEX][2])
    print(f"     tapped option #{SE_OPTION_INDEX + 1} '{option_name}' via {how}")
    time.sleep(2)


def step_se_ratio(driver):
    """
    Your Step 12 — ratio in every size field.
    heartbeat() per round: this loop can run 80-90s and runner-next.ts reaps a
    run after 90s without a database update. Typing logic unchanged.
    """
    run_id = RUNNER_STATUS.get("last_job_id")
    if not cat_wait_for_qty_boxes(driver):
        raise RuntimeError(
            "no size field found on this screen (waited 20s). On screen: "
            + cat_describe_screen(driver))
    filled, failed = {}, []
    for rnd in range(8):
        heartbeat(driver, run_id,
                  f"Entering ratio (pass {rnd + 1}/8, {len(filled)} fields done)")
        progress = False
        for hint, box in cat_get_qty_boxes(driver):
            key = hint or f"box@{box.rect['y']}"
            if key in filled:
                continue
            try:
                if cat_fill_box(box, SE_RATIO_VALUE):
                    filled[key] = SE_RATIO_VALUE
                    progress = True
                    print(f"     size {key} -> {SE_RATIO_VALUE}")
                else:
                    failed.append(key)
            except Exception as e:
                failed.append(f"{key} ({e})")
        cat_dismiss_keyboard(driver)
        visible = [h for h, _ in cat_get_qty_boxes(driver)]
        if not progress and visible and all(h in filled for h in visible):
            break
        cat_swipe_screen(driver, "up", ratio=0.4)

    if not filled:
        raise RuntimeError("no size field found on this screen")
    if failed:
        raise RuntimeError(f"{len(filled)} filled, these failed: {failed}")
    print(f"     [ok] Ratio '{SE_RATIO_VALUE}' entered in {len(filled)} size fields")


def step_se_plus(driver):
    """Your Step 13 — tap + to add the ratio."""
    buttons = cat_find_ratio_buttons(driver)
    for _ in range(4):
        if buttons:
            break
        cat_swipe_screen(driver, "up", ratio=0.4)
        buttons = cat_find_ratio_buttons(driver)
    if not buttons:
        raise RuntimeError("'+' button not found")
    before = cat_read_ratio_count(driver)
    how = cat_force_tap(driver, buttons[-1])        # rightmost = +
    print(f"     tapped '+' via {how}")
    time.sleep(1.5)
    after = cat_read_ratio_count(driver)
    print(f"     [ok] '+' tapped (ratio {before} -> {after})")


def step_se_add_to_cart(driver):
    """Your Step 14."""
    se_tap_add_to_cart(driver)


def step_se_back_1(driver):
    """Your Step 15."""
    tapped = se_tap_back_button(driver)
    print("     [ok] Back tapped" + ("" if tapped else " (system back)"))


def step_se_back_2(driver):
    """Your Step 16."""
    tapped = se_tap_back_button(driver)
    print("     [ok] Back tapped" + ("" if tapped else " (system back)"))


def step_se_home_1(driver):
    """Your Step 17."""
    cat_tap_with_retry(driver, AppiumBy.ANDROID_UIAUTOMATOR, SE_HOME_TAB_SELECTOR,
                       "Home tab (nav_home)", settle=2)


def step_se_reports_1(driver):
    """Your Step 18."""
    cat_tap_with_retry(driver, AppiumBy.ANDROID_UIAUTOMATOR, SE_REPORTS_TAB_SELECTOR,
                       "Reports tab (nav_reports)")


def step_se_size_wise_report(driver):
    """Your Step 19 — scrolls if needed."""
    card = cat_find_scrolling(driver, AppiumBy.ACCESSIBILITY_ID,
                              SE_SIZE_WISE_CATEGORY_DESC, max_swipes=8)
    if card is None:
        raise RuntimeError(
            f"'{SE_SIZE_WISE_CATEGORY_DESC}' not found even after scrolling")
    how = cat_force_tap(driver, card)
    print(f"     tapped '{SE_SIZE_WISE_CATEGORY_DESC}' via {how}")
    time.sleep(2)


def step_se_size_tile(driver):
    """Your Step 20."""
    cat_tap_with_retry(driver, AppiumBy.ACCESSIBILITY_ID, SE_TILE_DESC,
                       f"'{SE_SIZE_NUMBER}' tile", settle=2)


def step_se_checkbox(driver):
    """Your Step 21."""
    se_select_checkbox_2(driver)


def step_se_edit_sizes(driver):
    """
    Your Step 22 — edit EVERY size entry to SE_SIZE_VALUE.

    heartbeat() per edit: up to 10 edits, each with its own retry loops, can
    run for several minutes inside this one step. Without the heartbeat the
    run is reaped at 90s and reported as "Runner process stopped responding"
    while it is still working. The edit logic itself is unchanged.
    """
    run_id = RUNNER_STATUS.get("last_job_id")
    edited = 0
    while edited < SE_MAX_SIZE_EDITS:
        heartbeat(driver, run_id,
                  f"Editing size {SE_SIZE_NUMBER} entries ({edited} done)")
        if se_find_edit_size_button(driver) is None:
            break
        se_edit_one_size(driver, SE_SIZE_VALUE)
        edited += 1
        print(f"     size {SE_SIZE_NUMBER} entry #{edited} set to {SE_SIZE_VALUE}")
        time.sleep(1.5)
    if edited == 0:
        raise RuntimeError(f"no '{SE_EDIT_SIZE_DESC}' entry found")
    print(f"     [ok] {edited} size {SE_SIZE_NUMBER} entr(ies) set to '{SE_SIZE_VALUE}'")


def step_se_back_3(driver):
    """Your Step 23."""
    tapped = se_tap_back_button(driver)
    print("     [ok] Back tapped" + ("" if tapped else " (system back)"))


def step_se_home_2(driver):
    """Your Step 24."""
    cat_tap_with_retry(driver, AppiumBy.ANDROID_UIAUTOMATOR, SE_HOME_TAB_SELECTOR,
                       "Home tab (nav_home)", settle=2)


def step_se_reports_2(driver):
    """Your Step 25."""
    cat_tap_with_retry(driver, AppiumBy.ANDROID_UIAUTOMATOR, SE_REPORTS_TAB_SELECTOR,
                       "Reports tab (nav_reports)")


def step_se_brand_report(driver):
    """Your Step 26 — scrolls UPWARDS if needed."""
    locators = [
        (AppiumBy.ACCESSIBILITY_ID, SE_BRAND_REPORT_DESC),
        (AppiumBy.ANDROID_UIAUTOMATOR,
         f'new UiSelector().descriptionContains("{SE_BRAND_REPORT_DESC}")'),
    ]
    card = None
    for _ in range(5):
        for by, val in locators:
            elems = driver.find_elements(by, val)
            if elems:
                card = elems[0]
                break
        if card is not None:
            break
        print("     'Brand Report' not visible — scrolling up...")
        cat_swipe_screen(driver, "down", ratio=0.5)   # content moves down
    if card is None:
        raise RuntimeError("'Brand Report' not found even after scrolling up")
    how = cat_force_tap(driver, card)
    print(f"     tapped 'Brand Report' via {how}")
    time.sleep(2)


def step_se_report_option(driver):
    """Your Step 27 — open the option shown on the Brand Report."""
    report_option, how = se_tap_report_option(driver)
    print(f"     tapped option '{report_option}' via {how}")


def step_se_booking_page(driver):
    """Your Step 28 — tap the option name, stay on the booking page 5 sec."""
    booked, how = se_tap_report_option(driver, prefer=SE_STATE.get("option_name"))
    print(f"     tapped option '{booked}' via {how}")
    print("     [..] Staying on the booking page for 5 sec...")
    time.sleep(5)


def step_se_back_4(driver):
    """Your Step 29."""
    tapped = se_tap_back_button(driver)
    print("     [ok] Back tapped" + ("" if tapped else " (system back)"))


def step_se_back_5(driver):
    """Your Step 30."""
    tapped = se_tap_back_button(driver)
    print("     [ok] Back tapped" + ("" if tapped else " (system back)"))


def step_se_back_6(driver):
    """Your Step 31."""
    tapped = se_tap_back_button(driver)
    print("     [ok] Back tapped" + ("" if tapped else " (system back)"))


def step_se_back_brand_report(driver):
    """Your Step 32 — back on the Brand Report (top-left arrow)."""
    tapped = se_tap_back_button(driver)
    print("     [ok] Back tapped on Brand Report" +
          ("" if tapped else " (system back)"))


def step_se_home_3(driver):
    """Your Step 33."""
    cat_tap_with_retry(driver, AppiumBy.ANDROID_UIAUTOMATOR, SE_HOME_TAB_SELECTOR,
                       "Home tab (nav_home)", settle=2)


def step_se_cart_tab(driver):
    """Your Step 34 — reuses cat_open_cart_tab, which raises when the cart
    screen never opens instead of continuing silently into SAVE."""
    cat_open_cart_tab(driver)


def step_se_save(driver):
    """Your Step 35."""
    try:
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, "SAVE"))
        ).click()
    except Exception as e:
        raise RuntimeError(f"SAVE not clickable — cart empty? {e}")
    time.sleep(0.8)


def step_se_signature(driver):
    """Your Step 36."""
    draw_signature(driver)


def step_se_submit(driver):
    """Your Step 37."""
    WebDriverWait(driver, 30).until(
        EC.element_to_be_clickable((AppiumBy.ACCESSIBILITY_ID, "Submit"))
    ).click()


def step_se_order_saved(driver):
    """Your Step 38 — poll for Logout and click it the moment it renders."""
    elapsed = sf_smart_logout(driver)
    time.sleep(2)
    print(f"     [ok] Order saved (Logout appeared in {elapsed:.1f}s)")


def step_se_logout_done(driver):
    """Your Step 39. Your script slept 2s here and always passed; this uses
    your own wait_for_login_screen helper so the step can actually fail.
    Replace the body with time.sleep(2) to restore the original."""
    if not cat_wait_for_login_screen(driver):
        raise RuntimeError("Login screen did not appear after logout")


SIZE_EDIT = [
    # Login (reused hardened runner.py steps) — 8
    step_open_app, step_scan_qr, step_picker_open, step_tap_photo,
    step_done_picker, step_return_app, step_tap_login, step_wait_home,
    # Catalogue order — 9
    step_se_catalogue, step_se_brand, step_se_option, step_se_ratio,
    step_se_plus, step_se_add_to_cart, step_se_back_1, step_se_back_2,
    step_se_home_1,
    # Size Wise report + edits — 7
    step_se_reports_1, step_se_size_wise_report, step_se_size_tile,
    step_se_checkbox, step_se_edit_sizes, step_se_back_3, step_se_home_2,
    # Brand report — 8
    step_se_reports_2, step_se_brand_report, step_se_report_option,
    step_se_booking_page, step_se_back_4, step_se_back_5, step_se_back_6,
    step_se_back_brand_report,
    # Home -> cart -> submit -> logout — 7
    step_se_home_3, step_se_cart_tab, step_se_save, step_se_signature,
    step_se_submit, step_se_order_saved, step_se_logout_done,
]   # 39 steps


TEST_CASES: dict[str, list[Callable[[Any], None]]] = {
    "login_logout": LOGIN_LOGOUT,
    "product_deletion": PRODUCT_DELETION,
    "search_functionality": SEARCH_FUNCTIONALITY,
    "filter_functionality": FILTER_FUNCTIONALITY,
    "catalogue_order": CATALOGUE_ORDER,
    "size_edit": SIZE_EDIT,
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
        force_portrait(driver)
        scan_media(driver)

        failed_idx = None
        for idx, fn in enumerate(fns):
            rec.begin(idx)
            RUNNER_STATUS["last_step"] = step_names[idx] if idx < len(step_names) else f"step {idx+1}"
            heartbeat(driver, run_id, f"Step {idx + 1}/{len(fns)}: {RUNNER_STATUS['last_step']}")
            try:
                force_portrait(driver)
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
            RUNNER_STATUS["last_poll_at"] = _now_iso()
            job = db_select_queued()
            if job:
                RUNNER_STATUS["last_job_id"] = job.get("run_id")
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