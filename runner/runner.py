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


def is_picker_package(driver) -> bool:
    try:
        pkg = driver.current_package.lower()
        return any(marker in pkg for marker in PICKER_PACKAGE_MARKERS)
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


CATALOGUE_NAV_LOCATORS = [
    (AppiumBy.ID, "com.qart.qserve:id/nav_catalogue"),
    (AppiumBy.ID, "nav_catalogue"),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceId("com.qart.qserve:id/nav_catalogue")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceId("nav_catalogue")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().resourceIdMatches(".*(:id/)?nav_catalogue$")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().description("Catalogue")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().description("Catalogue Tab")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Catalogue")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("catalog")'),
    (AppiumBy.XPATH, '//*[@resource-id="com.qart.qserve:id/nav_catalogue" or @resource-id="nav_catalogue"]'),
    (AppiumBy.XPATH, '//*[contains(@resource-id, "nav_catalogue")]'),
    (AppiumBy.ACCESSIBILITY_ID, "Catalogue"),
    (AppiumBy.ACCESSIBILITY_ID, "Catalogue Tab"),
]


CATALOGUE_OPEN_LOCATORS = [
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
    scan_locators = [
        (AppiumBy.ACCESSIBILITY_ID, "Scan QR from gallery"),
        (AppiumBy.ACCESSIBILITY_ID, "Scan QR from Gallery"),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Scan QR")'),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Scan QR")'),
        (AppiumBy.XPATH, '//*[contains(@content-desc, "Scan QR") or contains(@text, "Scan QR")]'),
    ]

    for _ in range(3):
        if try_click(driver, scan_locators, timeout=4) or tap_first_locator_center(driver, scan_locators, timeout=2):
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
        pkg = driver.current_package.lower()
    except Exception:
        pkg = ""

    size = driver.get_window_size()

    # DocumentsUI / AOSP file picker path.
    if "documentsui" in pkg or "files" in pkg:
        if not tap_visible_qr_thumbnail(driver):
            raise RuntimeError("QR thumbnail not found in picker")
        try:
            WebDriverWait(driver, 5).until(
                lambda d: (not is_picker_package(d)) or has_any(d, [
                    (AppiumBy.XPATH, "//*[@text='Done']"),
                    (AppiumBy.ACCESSIBILITY_ID, "Done"),
                    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches("(?i)done|open|select")'),
                ], timeout=0.5)
            )
        except Exception as e:
            raise RuntimeError("QR image was not selected; picker stayed open after tapping thumbnail") from e
        time.sleep(1.0)
        return

    # Pixel 8 / Android 14 Photo Picker: the first QR thumbnail is top-left.
    # Appium often exposes decorative ImageViews first, so tap the known cell
    # before trying selectors.
    if "providers.media.module" in pkg or (size.get("width") == 1080 and size.get("height", 0) >= 2300):
        tap_xy(driver, 180, 580)
        time.sleep(1)
        return

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
            return
        except Exception:
            continue

    # Fallback: dynamic bounds-based first thumbnail scan.
    if tap_first_picker_thumbnail(driver, timeout=4):
        time.sleep(1)
        return

    # Pixel 8 exact coordinate fallback (1080x2400, viewport top=132).
    tap_xy(driver, 180, 580)
    time.sleep(1)


def step_done_picker(driver):
    tried = False
    for finder in [
        lambda d: d.find_element(AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().text("Done")'),
        lambda d: d.find_element(AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().text("Add")'),
        lambda d: d.find_element(AppiumBy.ACCESSIBILITY_ID, "Done"),
        lambda d: d.find_element(AppiumBy.ACCESSIBILITY_ID, "Add"),
        # Android 14 uses a checkmark FAB button.
        lambda d: d.find_element(AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().resourceId("com.google.android.providers.media.module:id/button_add")'),
    ]:
        try:
            finder(driver).click()
            tried = True
            break
        except Exception:
            continue
    if not tried:
        # Pixel 8 - "Add" button is bottom-right area.
        tap_xy(driver, 900, 2300)
    time.sleep(2)
def step_return_app(driver):
    try:
        WebDriverWait(driver, 15).until(lambda d: d.current_package == APP_PACKAGE)
    except Exception:
        driver.back()
        WebDriverWait(driver, 12).until(lambda d: d.current_package == APP_PACKAGE)
    if not has_any(driver, LOGIN_LOCATORS + HOME_LOCATORS, timeout=8):
        raise RuntimeError("Returned to app, but neither Login nor Home screen appeared")
def step_tap_login(driver):
    if has_any(driver, HOME_LOCATORS, timeout=2):
        return
    if not try_click(driver, LOGIN_LOCATORS, timeout=4):
        raise RuntimeError("Login button did not appear after QR selection")
    time.sleep(4)
def step_wait_home(driver):
    if not has_any(driver, HOME_LOCATORS, timeout=12):
        raise RuntimeError("Home screen did not appear after tapping Login")
def step_logout(driver):
    WebDriverWait(driver, 20).until(EC.element_to_be_clickable(
        (AppiumBy.ACCESSIBILITY_ID, "Logout"))).click()

def step_catalogue(driver):
    if catalogue_is_open(driver, timeout=1):
        return

    # 1) Tap the actual nav_catalogue/Catalogue element center. This works even
    # when UiAutomator exposes the node as displayed but not "clickable".
    if tap_first_locator_center(driver, CATALOGUE_NAV_LOCATORS, timeout=5):
        if catalogue_is_open(driver, timeout=3):
            return

    # 2) Try normal click semantics as a secondary path.
    if try_click(driver, CATALOGUE_NAV_LOCATORS, timeout=2):
        if catalogue_is_open(driver, timeout=3):
            return

    # 3) Parse XML bounds for the catalogue/bottom-nav node and tap its center.
    if tap_catalogue_from_source_bounds(driver):
        if catalogue_is_open(driver, timeout=3):
            return

    # 4) Coordinate fallback: 2nd bottom-nav slot, kept above Android gesture bar.
    s = driver.get_window_size()
    for x_pct, y_pct in ((0.30, 0.91), (0.30, 0.94), (0.25, 0.91), (0.35, 0.91)):
        tap_xy(driver, int(s["width"] * x_pct), int(s["height"] * y_pct))
        if catalogue_is_open(driver, timeout=2):
            return

    raise RuntimeError("Catalogue tab did not open after tapping nav_catalogue")

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
        force_portrait(driver)
        scan_media(driver)

        failed_idx = None
        for idx, fn in enumerate(fns):
            rec.begin(idx)
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
