#!/usr/bin/env python3
"""Runtime patcher: select the Google Meet browser backend.

Upstream google_meet launches a local Playwright-managed Chromium. Spartan Gate
already provides Browserless endpoints, including an optional persistent main
profile broker, and can also run generic browser work through Camofox. This
patch keeps Browserless/CDP behavior intact and routes transcribe-mode Meet
sessions through Camofox when CAMOFOX_URL is configured.
"""

from __future__ import annotations

import sys
from pathlib import Path

MEET_BOT_TARGET = Path('/opt/hermes/plugins/google_meet/meet_bot.py')
CLI_TARGET = Path('/opt/hermes/plugins/google_meet/cli.py')

MEET_BOT_MARKER = '# Spartan Gate patch: Google Meet CDP browser selection'
CLI_MARKER = '# Spartan Gate patch: Google Meet setup accepts Browserless CDP'

MEET_BOT_HELPER_ANCHOR = '''SAY_PCM_FILENAME = "speaker.pcm"


def _is_safe_meet_url(url: str) -> bool:
'''

MEET_BOT_HELPER_BLOCK = '''SAY_PCM_FILENAME = "speaker.pcm"


def _meet_camofox_mode_enabled() -> bool:
    return bool(os.environ.get("CAMOFOX_URL", "").strip())


def _resolve_meet_cdp() -> tuple[str, str]:
    """Return (cdp_url, profile_mode) for the Meet browser.

    When CAMOFOX_URL is configured, Meet runs through Camofox and stale CDP
    values are ignored. Realtime/audio remains Browserless/CDP-only; run_bot()
    downgrades realtime requests in Camofox mode to transcribe.

    HERMES_MEET_CDP_URL is an explicit override. Otherwise
    HERMES_MEET_CDP_PROFILE=main, or the configured BROWSERLESS_PROFILE name,
    selects the Browserless persistent-profile broker. Guest/empty selects the
    normal ephemeral Browserless launch.
    """
    # Spartan Gate patch: Google Meet CDP browser selection
    if _meet_camofox_mode_enabled():
        return "", "camofox"

    explicit = os.environ.get("HERMES_MEET_CDP_URL", "").strip()
    if explicit:
        return explicit, "custom"

    profile = os.environ.get("HERMES_MEET_CDP_PROFILE", "guest").strip().lower()
    browserless_profile = os.environ.get("BROWSERLESS_PROFILE", "main").strip().lower()
    if profile == "main" or (browserless_profile and profile == browserless_profile):
        return os.environ.get("BROWSER_CDP_MAIN_URL", "").strip(), "main"

    return os.environ.get("BROWSER_CDP_URL", "").strip(), "guest"


def _camofox_request_headers() -> dict:
    access_key = os.environ.get("CAMOFOX_ACCESS_KEY", "").strip()
    if not access_key:
        return {}
    return {"Authorization": f"Bearer {access_key}"}


def _camofox_extract_result(payload):
    if isinstance(payload, dict):
        if "error" in payload and payload.get("error"):
            raise RuntimeError(str(payload.get("error")))
        for key in ("result", "value", "data"):
            if key in payload:
                return payload.get(key)
    return payload


class _CamofoxMeetClient:
    def __init__(self) -> None:
        import requests

        self.requests = requests
        self.base = os.environ.get("CAMOFOX_URL", "").rstrip("/")
        if not self.base:
            raise RuntimeError("CAMOFOX_URL is required for Camofox Meet backend")
        self.user_id = os.environ.get("CAMOFOX_USER_ID", "").strip() or "spartan-camofox-main"
        self.session_key = (
            os.environ.get("HERMES_MEET_CAMOFOX_SESSION_KEY", "").strip()
            or "meet"
        )
        self.tab_id = None

    def request(self, method: str, path: str, *, json_body=None, timeout: int = 30):
        url = f"{self.base}{path}"
        response = self.requests.request(
            method,
            url,
            json=json_body,
            headers=_camofox_request_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        text = response.text
        try:
            return json.loads(text) if text else None
        except Exception:
            return text

    def open_tab(self, url: str):
        payload = self.request(
            "POST",
            "/tabs",
            json_body={
                "userId": self.user_id,
                "sessionKey": self.session_key,
                "url": url,
            },
            timeout=60,
        )
        tab = payload.get("tab") if isinstance(payload, dict) else None
        candidates = []
        if isinstance(payload, dict):
            candidates.extend([payload.get("tabId"), payload.get("id"), payload.get("targetId")])
        if isinstance(tab, dict):
            candidates.extend([tab.get("tabId"), tab.get("id"), tab.get("targetId")])
        self.tab_id = next((str(value) for value in candidates if value), None)
        if not self.tab_id:
            raise RuntimeError(f"Camofox did not return a tab id: {payload!r}")
        return payload

    def evaluate(self, expression: str, timeout: int = 30):
        if not self.tab_id:
            raise RuntimeError("Camofox tab is not open")
        payload = self.request(
            "POST",
            f"/tabs/{self.tab_id}/evaluate",
            json_body={"userId": self.user_id, "expression": expression},
            timeout=timeout,
        )
        return _camofox_extract_result(payload)

    def press(self, key: str):
        if not self.tab_id:
            raise RuntimeError("Camofox tab is not open")
        try:
            return self.request(
                "POST",
                f"/tabs/{self.tab_id}/press",
                json_body={"userId": self.user_id, "key": key},
                timeout=10,
            )
        except Exception:
            expression = r"""
            (key) => {
              const parts = String(key || '').split('+');
              const finalKey = parts[parts.length - 1] || key;
              const init = {
                key: finalKey.length === 1 ? finalKey.toLowerCase() : finalKey,
                code: finalKey.length === 1 ? `Key${finalKey.toUpperCase()}` : finalKey,
                bubbles: true,
                ctrlKey: parts.includes('Control'),
                metaKey: parts.includes('Meta'),
                altKey: parts.includes('Alt'),
                shiftKey: parts.includes('Shift'),
              };
              document.dispatchEvent(new KeyboardEvent('keydown', init));
              document.body && document.body.dispatchEvent(new KeyboardEvent('keydown', init));
              document.dispatchEvent(new KeyboardEvent('keyup', init));
              document.body && document.body.dispatchEvent(new KeyboardEvent('keyup', init));
              return true;
            }
            """
            return self.evaluate(f"({expression})({json.dumps(key)})")

    def screenshot(self, path: str, *, full_page: bool = True):
        if not self.tab_id:
            raise RuntimeError("Camofox tab is not open")
        import base64

        response = self.requests.post(
            f"{self.base}/tabs/{self.tab_id}/screenshot",
            json={"userId": self.user_id, "fullPage": full_page},
            headers=_camofox_request_headers(),
            timeout=30,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        data = response.content
        if "application/json" in content_type:
            payload = response.json()
            raw = (
                payload.get("screenshot")
                or payload.get("image")
                or payload.get("data")
                or payload.get("base64")
                or ""
            )
            if isinstance(raw, str) and raw.startswith("data:"):
                raw = raw.split(",", 1)[1]
            data = base64.b64decode(raw) if raw else b""
        if path and data:
            Path(path).write_bytes(data)
        return data

    def close(self):
        if not self.tab_id:
            return
        try:
            self.request("DELETE", f"/tabs/{self.tab_id}", json_body={"userId": self.user_id}, timeout=10)
        except Exception:
            pass
        self.tab_id = None


class _CamofoxKeyboard:
    def __init__(self, page: "_CamofoxMeetPage") -> None:
        self.page = page

    def press(self, key: str):
        return self.page.client.press(key)


class _CamofoxLocator:
    def __init__(self, page: "_CamofoxMeetPage", selector: str, index: int = 0) -> None:
        self.page = page
        self.selector = selector
        self.index = index

    @property
    def first(self):
        return _CamofoxLocator(self.page, self.selector, 0)

    def nth(self, index: int):
        return _CamofoxLocator(self.page, self.selector, index)

    def count(self):
        return int(self.page.evaluate(r"""
        ({selector, index}) => {
          try {
            const els = Array.from(document.querySelectorAll(selector));
            if (index === null || index === undefined) return els.length;
            return els[index] ? 1 : 0;
          } catch (_) {
            return 0;
          }
        }
        """, {"selector": self.selector, "index": self.index}) or 0)

    def is_visible(self):
        return bool(self.page.evaluate(r"""
        ({selector, index}) => {
          const el = Array.from(document.querySelectorAll(selector))[index || 0];
          if (!el) return false;
          const rect = el.getBoundingClientRect();
          const style = window.getComputedStyle(el);
          return !!(rect.width || rect.height) && style.visibility !== 'hidden' && style.display !== 'none';
        }
        """, {"selector": self.selector, "index": self.index}))

    def fill(self, value: str, timeout: int = 0):
        return self.page.evaluate(r"""
        ({selector, index, value}) => {
          const el = Array.from(document.querySelectorAll(selector))[index || 0];
          if (!el) return false;
          el.focus();
          el.value = value;
          el.dispatchEvent(new Event('input', {bubbles: true}));
          el.dispatchEvent(new Event('change', {bubbles: true}));
          return true;
        }
        """, {"selector": self.selector, "index": self.index, "value": value})

    def click(self, timeout: int = 0):
        return self.page.evaluate(r"""
        ({selector, index}) => {
          const el = Array.from(document.querySelectorAll(selector))[index || 0];
          if (!el) return false;
          el.click();
          return true;
        }
        """, {"selector": self.selector, "index": self.index})


class _CamofoxRoleLocator:
    def __init__(self, page: "_CamofoxMeetPage", role: str, name: str = "", exact: bool = False, index: int = 0) -> None:
        self.page = page
        self.role = role
        self.name = name or ""
        self.exact = exact
        self.index = index

    @property
    def first(self):
        return _CamofoxRoleLocator(self.page, self.role, self.name, self.exact, 0)

    def _expression(self, action: str):
        return self.page.evaluate(r"""
        ({role, name, exact, index, action}) => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return !!(rect.width || rect.height) && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const norm = (value) => (value || '').toLocaleLowerCase()
            .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim();
          const selector = role === 'button' ? 'button,[role="button"]' : `[role="${role}"]`;
          const needle = norm(name);
          const matches = Array.from(document.querySelectorAll(selector)).filter((el) => {
            const label = norm(`${el.innerText || ''} ${el.getAttribute('aria-label') || ''}`);
            if (!needle) return true;
            return exact ? label === needle : label.includes(needle);
          });
          if (action === 'count') return matches.length ? 1 : 0;
          const el = matches[index || 0];
          if (!el) return false;
          if (action === 'visible') return visible(el);
          if (action === 'click') {
            if (!visible(el) || el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
            el.click();
            return true;
          }
          return false;
        }
        """, {"role": self.role, "name": self.name, "exact": self.exact, "index": self.index, "action": action})

    def count(self):
        return int(self._expression("count") or 0)

    def is_visible(self):
        return bool(self._expression("visible"))

    def click(self, timeout: int = 0):
        return self._expression("click")


class _CamofoxMeetPage:
    def __init__(self) -> None:
        self.client = _CamofoxMeetClient()
        self.keyboard = _CamofoxKeyboard(self)
        self._closed = False

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000):
        self.client.open_tab(url)
        if wait_until:
            self.wait_for_timeout(2000)
        return None

    def evaluate(self, source: str, arg=None):
        expression = source
        if arg is not None:
            expression = f"({source})({json.dumps(arg)})"
        return self.client.evaluate(expression)

    def locator(self, selector: str):
        return _CamofoxLocator(self, selector, 0)

    def get_by_role(self, role: str, *, name: str = "", exact: bool = False):
        return _CamofoxRoleLocator(self, role, name, exact, 0)

    def wait_for_timeout(self, ms: int):
        time.sleep(max(0, ms) / 1000)

    def screenshot(self, path: str, full_page: bool = True):
        return self.client.screenshot(path, full_page=full_page)

    def is_closed(self):
        return self._closed

    def close(self):
        self._closed = True
        self.client.close()


class _CamofoxContext:
    def __init__(self) -> None:
        self.pages = []

    def new_page(self):
        page = _CamofoxMeetPage()
        self.pages.append(page)
        return page

    def grant_permissions(self, *args, **kwargs):
        return None

    def close(self):
        for page in list(self.pages):
            try:
                page.close()
            except Exception:
                pass


class _CamofoxBrowser:
    contexts = []

    def new_context(self, **kwargs):
        return _CamofoxContext()

    def close(self):
        return None


class _CamofoxChromium:
    def launch(self, **kwargs):
        return _CamofoxBrowser()


class _CamofoxPlaywright:
    chromium = _CamofoxChromium()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _camofox_sync_playwright():
    return _CamofoxPlaywright()


def _is_safe_meet_url(url: str) -> bool:
'''

MEET_BOT_OLD_LAUNCH_BLOCK = '''    try:
        with sync_playwright() as pw:
            # Playwright's launch() doesn't take env; we set PULSE_SOURCE
            # via the process env before launch so the child Chrome inherits it.
            for k, v in chrome_env.items():
                os.environ[k] = v
            browser = pw.chromium.launch(
                headless=not headed,
                args=chrome_args,
            )
            context_args = {
                "viewport": {"width": 1280, "height": 800},
                "user_agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "permissions": ["microphone", "camera"],
            }
            if auth_state and Path(auth_state).is_file():
                context_args["storage_state"] = auth_state
            context = browser.new_context(**context_args)
            page = context.new_page()
'''

MEET_BOT_REALTIME_BLOCK = '''    if rt["enabled"]:
        if not realtime_api_key:
'''

MEET_BOT_REALTIME_REPLACEMENT = '''    if rt["enabled"] and _meet_camofox_mode_enabled():
        state.set(error="realtime mode requested with Camofox backend - falling back to transcribe")
        rt["enabled"] = False
    if rt["enabled"]:
        if not realtime_api_key:
'''

MEET_BOT_OLD_IMPORT_BLOCK = '''    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        state.set(error=f"playwright not installed: {e}", exited=True)
        sys.stderr.write(
            "google_meet bot: playwright is not installed. Run "
            "`pip install playwright && python -m playwright install chromium`\\n"
        )
        if rt["bridge"]:
            rt["bridge"].teardown()
        return 3
'''

MEET_BOT_NEW_IMPORT_BLOCK = '''    if _meet_camofox_mode_enabled():
        sync_playwright = _camofox_sync_playwright
    else:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            state.set(error=f"playwright not installed: {e}", exited=True)
            sys.stderr.write(
                "google_meet bot: playwright is not installed. Run "
                "`pip install playwright && python -m playwright install chromium`\\n"
            )
            if rt["bridge"]:
                rt["bridge"].teardown()
            return 3
'''

MEET_BOT_NEW_LAUNCH_BLOCK = '''    cdp_url, cdp_profile = _resolve_meet_cdp()
    cdp_shared_context = False

    try:
        with sync_playwright() as pw:
            context_args = {
                "viewport": {"width": 1280, "height": 800},
                "permissions": ["microphone", "camera"],
            }
            if cdp_url:
                # Spartan Gate patch: Google Meet CDP browser selection
                browser = pw.chromium.connect_over_cdp(cdp_url)
                if browser.contexts:
                    context = browser.contexts[0]
                    cdp_shared_context = True
                else:
                    if auth_state and Path(auth_state).is_file():
                        context_args["storage_state"] = auth_state
                    context = browser.new_context(**context_args)
                try:
                    context.grant_permissions(["microphone", "camera"], origin="https://meet.google.com")
                except Exception:
                    pass
            else:
                # Playwright's launch() doesn't take env; we set PULSE_SOURCE
                # via the process env before launch so the child Chrome inherits it.
                for k, v in chrome_env.items():
                    os.environ[k] = v
                browser = pw.chromium.launch(
                    headless=not headed,
                    args=chrome_args,
                )
                context_args["user_agent"] = (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                if auth_state and Path(auth_state).is_file():
                    context_args["storage_state"] = auth_state
                context = browser.new_context(**context_args)
            page = context.new_page()
'''

MEET_BOT_OLD_TEARDOWN_BLOCK = '''            context.close()
            browser.close()
'''

MEET_BOT_NEW_TEARDOWN_BLOCK = '''            try:
                page.close()
            except Exception:
                pass
            if cdp_url:
                # Leave persistent Browserless contexts open; closing the page
                # is enough for shared main-profile sessions. Ephemeral/custom
                # CDP sessions close the browser connection to release Browserless.
                if cdp_profile != "main":
                    try:
                        browser.close()
                    except Exception:
                        pass
            else:
                context.close()
                browser.close()
'''
MEET_BOT_LOCALE_MARKER = '# Spartan Gate patch: Google Meet localized join diagnostics'
MEET_BOT_STATE_FIELDS_ANCHOR = '        self.lobby_waiting = False\n        self.join_attempted_at: Optional[float] = None\n'
MEET_BOT_STATE_FIELDS_BLOCK = '        self.lobby_waiting = False\n        self.guest_name_attempted = False\n        self.guest_name_filled = False\n        self.guest_name_error: Optional[str] = None\n        self.join_button_found = False\n        self.join_button_clicked: Optional[str] = None\n        self.join_button_error: Optional[str] = None\n        self.last_debug_capture: Optional[str] = None\n        self.debug_captures: list[dict] = []\n        self.join_attempted_at: Optional[float] = None\n'
MEET_BOT_STATUS_FIELDS_ANCHOR = '            "lobbyWaiting": self.lobby_waiting,\n            "joinAttemptedAt": self.join_attempted_at,\n'
MEET_BOT_STATUS_FIELDS_BLOCK = '            "lobbyWaiting": self.lobby_waiting,\n            "guestNameAttempted": self.guest_name_attempted,\n            "guestNameFilled": self.guest_name_filled,\n            "guestNameError": self.guest_name_error,\n            "joinButtonFound": self.join_button_found,\n            "joinButtonClicked": self.join_button_clicked,\n            "joinButtonError": self.join_button_error,\n            "lastDebugCapture": self.last_debug_capture,\n            "debugCaptures": self.debug_captures,\n            "joinAttemptedAt": self.join_attempted_at,\n'
MEET_BOT_JOIN_CALL_BLOCK = '            _try_guest_name(page, guest_name)\n            _click_join(page, state)\n'
MEET_BOT_JOIN_CALL_REPLACEMENT = '            _try_guest_name(page, guest_name, state)\n            _click_join(page, state)\n'
MEET_BOT_READY_MARKER = '# Spartan Gate patch: Google Meet pre-join readiness wait'
MEET_BOT_READY_JOIN_CALL_BLOCK = '            _try_guest_name(page, guest_name, state)\n            _click_join(page, state)\n'
MEET_BOT_READY_JOIN_CALL_REPLACEMENT = '            if _wait_for_join_controls(page, state):\n                if not _detect_admission(page):\n                    _try_guest_name(page, guest_name, state)\n                if not _detect_admission(page) and _wait_for_join_controls(page, state, timeout_ms=60_000, require_join_button=True):\n                    _click_join(page, state)\n'
MEET_BOT_DENIED_BLOCK = '                    elif _detect_denied(page):\n                        state.set(\n                            error="host denied admission",\n                            leave_reason="denied",\n                        )\n                        break\n'
MEET_BOT_DENIED_REPLACEMENT = '                    else:\n                        denied_reason = _detect_denied(page)\n                        if denied_reason:\n                            _store_debug_capture(\n                                state,\n                                _capture_debug(page, out_dir, f"admission_{denied_reason}"),\n                            )\n                            if denied_reason == "no_response":\n                                state.set(\n                                    error="no one admitted the bot",\n                                    leave_reason="no_response",\n                                )\n                            elif denied_reason == "removed":\n                                state.set(\n                                    error="bot was removed from the meeting",\n                                    leave_reason="removed",\n                                )\n                            elif denied_reason == "meeting_ended":\n                                state.set(\n                                    error="meeting ended before admission",\n                                    leave_reason="meeting_ended",\n                                )\n                            elif denied_reason == "invalid_link":\n                                state.set(\n                                    error="invalid or expired Meet link",\n                                    leave_reason="invalid_link",\n                                )\n                            elif denied_reason == "cannot_join":\n                                state.set(\n                                    error="join blocked/cannot join",\n                                    leave_reason="cannot_join",\n                                )\n                            else:\n                                state.set(\n                                    error="host denied admission",\n                                    leave_reason="denied",\n                                )\n                            break\n'
MEET_BOT_FINAL_STATUS_BLOCK = '            state.set(in_call=False, captioning=False, exited=True)\n            return 0\n'
MEET_BOT_FINAL_STATUS_REPLACEMENT = '            if not state.joined_at:\n                _store_debug_capture(\n                    state,\n                    _capture_debug(page, out_dir, state.leave_reason or "not_joined"),\n                )\n                if not state.error:\n                    state.set(error="join blocked/unknown")\n            state.set(in_call=False, captioning=False, exited=True)\n            return 0\n'
MEET_BOT_OLD_TRY_GUEST_NAME_BLOCK = 'def _try_guest_name(page, guest_name: str) -> None:\n    """If Meet is showing a guest-name input, type *guest_name* into it."""\n    try:\n        # Meet\'s guest name input has placeholder "Your name".\n        locator = page.locator(\'input[aria-label*="name" i]\').first\n        if locator.count() and locator.is_visible():\n            locator.fill(guest_name, timeout=2_000)\n    except Exception:\n        pass\n'
MEET_BOT_NEW_TRY_GUEST_NAME_BLOCK = 'def _sanitize_debug_reason(reason: str) -> str:\n    reason = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(reason or "unknown")).strip("._")\n    return reason[:80] or "unknown"\n\n\ndef _capture_debug(page, out_dir: Path, reason: str) -> dict:\n    \'\'\'Write a small Meet DOM/screenshot bundle for admission debugging.\'\'\'\n    # Spartan Gate patch: Google Meet localized join diagnostics\n    safe_reason = _sanitize_debug_reason(reason)\n    out_dir.mkdir(parents=True, exist_ok=True)\n    result = {"reason": safe_reason}\n\n    screenshot_path = out_dir / f"debug_{safe_reason}.png"\n    text_path = out_dir / f"debug_{safe_reason}_text.txt"\n    buttons_path = out_dir / f"debug_{safe_reason}_buttons.json"\n    inputs_path = out_dir / f"debug_{safe_reason}_inputs.json"\n\n    try:\n        page.screenshot(path=str(screenshot_path), full_page=True)\n        result["screenshot"] = str(screenshot_path)\n    except Exception as e:\n        result["screenshotError"] = str(e)\n\n    try:\n        text = page.evaluate("() => document.body ? document.body.innerText || \'\' : \'\'")\n        text_path.write_text(str(text or ""), encoding="utf-8")\n        result["text"] = str(text_path)\n    except Exception as e:\n        result["textError"] = str(e)\n\n    try:\n        buttons = page.evaluate(r\'\'\'\n        () => {\n          const visible = (el) => {\n            const rect = el.getBoundingClientRect();\n            const style = window.getComputedStyle(el);\n            return !!(rect.width || rect.height) && style.visibility !== \'hidden\' && style.display !== \'none\';\n          };\n          return Array.from(document.querySelectorAll(\'button,[role="button"]\'))\n            .map((el) => ({\n              innerText: el.innerText || \'\',\n              ariaLabel: el.getAttribute(\'aria-label\') || \'\',\n              role: el.getAttribute(\'role\') || \'\',\n              visible: visible(el),\n              enabled: !el.disabled && el.getAttribute(\'aria-disabled\') !== \'true\',\n            }))\n            .filter((entry) => entry.innerText || entry.ariaLabel)\n            .slice(0, 80);\n        }\n        \'\'\')\n        buttons_path.write_text(json.dumps(buttons, ensure_ascii=False, indent=2), encoding="utf-8")\n        result["buttons"] = str(buttons_path)\n    except Exception as e:\n        result["buttonsError"] = str(e)\n\n    try:\n        inputs = page.evaluate(r\'\'\'\n        () => {\n          const visible = (el) => {\n            const rect = el.getBoundingClientRect();\n            const style = window.getComputedStyle(el);\n            return !!(rect.width || rect.height) && style.visibility !== \'hidden\' && style.display !== \'none\';\n          };\n          return Array.from(document.querySelectorAll(\'input,textarea\'))\n            .map((el) => ({\n              tag: el.tagName.toLowerCase(),\n              type: el.getAttribute(\'type\') || \'\',\n              ariaLabel: el.getAttribute(\'aria-label\') || \'\',\n              placeholder: el.getAttribute(\'placeholder\') || \'\',\n              valueLength: (el.value || \'\').length,\n              visible: visible(el),\n              enabled: !el.disabled && el.getAttribute(\'aria-disabled\') !== \'true\',\n            }))\n            .filter((entry) => entry.visible || entry.ariaLabel || entry.placeholder)\n            .slice(0, 80);\n        }\n        \'\'\')\n        inputs_path.write_text(json.dumps(inputs, ensure_ascii=False, indent=2), encoding="utf-8")\n        result["inputs"] = str(inputs_path)\n    except Exception as e:\n        result["inputsError"] = str(e)\n\n    return result\n\n\ndef _store_debug_capture(state: "_BotState", capture: dict) -> None:\n    if not capture:\n        return\n    captures = list(getattr(state, "debug_captures", []) or [])\n    captures.append(capture)\n    primary = capture.get("screenshot") or capture.get("text") or capture.get("reason")\n    state.set(debug_captures=captures, last_debug_capture=primary)\n\n\ndef _page_seems_guest_name_screen(page) -> bool:\n    try:\n        text = page.evaluate("() => document.body ? document.body.innerText || \'\' : \'\'")\n    except Exception:\n        return False\n    return bool(re.search(\n        r"your name|name|join now|ask to join|tu nombre|nombre|unirse|unirte|solicitar|pedir",\n        str(text or ""),\n        re.I,\n    ))\n\n\ndef _try_guest_name(page, guest_name: str, state: Optional["_BotState"] = None) -> None:\n    \'\'\'If Meet is showing a localized guest-name input, type *guest_name*.\'\'\'\n    selectors = [\n        \'input[aria-label*="name" i]\',\n        \'input[aria-label*="nombre" i]\',\n        \'input[placeholder*="name" i]\',\n        \'input[placeholder*="nombre" i]\',\n        \'textarea[aria-label*="name" i]\',\n        \'textarea[aria-label*="nombre" i]\',\n        \'textarea[placeholder*="name" i]\',\n        \'textarea[placeholder*="nombre" i]\',\n    ]\n    errors = []\n    if state:\n        state.set(guest_name_attempted=True)\n\n    for selector in selectors:\n        try:\n            locator = page.locator(selector).first\n            if locator.count() and locator.is_visible():\n                locator.fill(guest_name, timeout=2_000)\n                if state:\n                    state.set(guest_name_filled=True, guest_name_error=None)\n                return\n        except Exception as e:\n            errors.append(f"{selector}: {e}")\n\n    try:\n        if _page_seems_guest_name_screen(page):\n            candidates = page.locator(\'input[type="text"], input:not([type]), textarea\')\n            for index in range(min(candidates.count(), 8)):\n                candidate = candidates.nth(index)\n                if candidate.is_visible():\n                    candidate.fill(guest_name, timeout=2_000)\n                    if state:\n                        state.set(guest_name_filled=True, guest_name_error=None)\n                    return\n    except Exception as e:\n        errors.append(f"fallback: {e}")\n\n    if state:\n        message = "guest name input not found"\n        if errors:\n            message += "; " + "; ".join(errors[-3:])\n        if _page_seems_guest_name_screen(page):\n            _store_debug_capture(state, _capture_debug(page, state.out_dir, "guest_name_input_not_found"))\n        state.set(guest_name_filled=False, guest_name_error=message)\n'
MEET_BOT_READY_HELPER_ANCHOR = '\n\ndef _try_guest_name(page, guest_name: str, state: Optional["_BotState"] = None) -> None:\n'
MEET_BOT_READY_HELPER_BLOCK = '\n\ndef _join_controls_ready(page, *, require_join_button: bool = False) -> bool:\n    if _detect_admission(page):\n        return True\n    try:\n        return bool(page.evaluate(r\'\'\'\n        ({labels, requireJoinButton}) => {\n          const visible = (el) => {\n            if (!el) return false;\n            const rect = el.getBoundingClientRect();\n            const style = window.getComputedStyle(el);\n            return !!(rect.width || rect.height) && style.visibility !== \'hidden\' && style.display !== \'none\';\n          };\n          const enabled = (el) => !el.disabled && el.getAttribute(\'aria-disabled\') !== \'true\';\n          if (!requireJoinButton) {\n            const inputSelectors = [\n              \'input[aria-label*=\"name\" i]\',\n              \'input[aria-label*=\"nombre\" i]\',\n              \'input[placeholder*=\"name\" i]\',\n              \'input[placeholder*=\"nombre\" i]\',\n              \'textarea[aria-label*=\"name\" i]\',\n              \'textarea[aria-label*=\"nombre\" i]\',\n              \'textarea[placeholder*=\"name\" i]\',\n              \'textarea[placeholder*=\"nombre\" i]\',\n            ];\n            const inputs = inputSelectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)));\n            if (inputs.some((el) => visible(el) && enabled(el))) return true;\n          }\n          const norm = (value) => (value || \'\').toLocaleLowerCase()\n            .normalize(\'NFD\').replace(/[\\u0300-\\u036f]/g, \'\');\n          const buttons = Array.from(document.querySelectorAll(\'button,[role="button"]\'));\n          for (const label of labels) {\n            const needle = norm(label);\n            if (buttons.some((el) => {\n              if (!visible(el) || !enabled(el)) return false;\n              const text = norm(`${el.innerText || \'\'} ${el.getAttribute(\'aria-label\') || \'\'}`);\n              return text.includes(needle);\n            })) return true;\n          }\n          return false;\n        }\n        \'\'\', {"labels": list(_JOIN_BUTTONS), "requireJoinButton": require_join_button}))\n    except Exception:\n        return False\n\n\ndef _wait_for_join_controls(\n    page,\n    state: "_BotState",\n    *,\n    timeout_ms: int = 60_000,\n    require_join_button: bool = False,\n) -> bool:\n    # Spartan Gate patch: Google Meet pre-join readiness wait\n    deadline = time.time() + (timeout_ms / 1000)\n    while time.time() < deadline:\n        if _join_controls_ready(page, require_join_button=require_join_button):\n            return True\n        if _detect_denied(page):\n            return False\n        try:\n            page.wait_for_timeout(1_000)\n        except Exception:\n            time.sleep(1)\n\n    reason = "join_button_not_ready_timeout" if require_join_button else "join_controls_not_ready_timeout"\n    _store_debug_capture(state, _capture_debug(page, state.out_dir, reason))\n    state.set(\n        join_button_found=False,\n        join_button_error=f"join controls not ready after {timeout_ms}ms",\n    )\n    return False\n\n\ndef _try_guest_name(page, guest_name: str, state: Optional["_BotState"] = None) -> None:\n'
MEET_BOT_OLD_DETECT_ADMISSION_BLOCK = 'def _detect_admission(page) -> bool:\n    """True if we\'re clearly past the lobby and in the call itself.\n\n    Uses a JS-side probe because Meet\'s DOM structure varies by client\n    version. We check several high-signal indicators and declare admission\n    on the first hit:\n\n      1. Leave-call button is present (``aria-label`` contains "eave call").\n      2. Caption region has appeared (we installed the observer and it attached).\n      3. The participant list container is visible.\n\n    Conservative by default — returns False on any error.\n    """\n    probe = r"""\n    (() => {\n      const leave = document.querySelector(\'button[aria-label*="eave call" i]\');\n      if (leave) return true;\n      if (window.__hermesMeetInstalled) {\n        const caps = document.querySelector(\n          \'[role="region"][aria-label*="aption" i], \' +\n          \'div[jsname="YSxPC"], div[jsname="tgaKEf"]\'\n        );\n        if (caps) return true;\n      }\n      const parts = document.querySelector(\'[aria-label*="articipants" i]\');\n      if (parts) return true;\n      return false;\n    })();\n    """\n    try:\n        return bool(page.evaluate(probe))\n    except Exception:\n        return False\n'
MEET_BOT_NEW_DETECT_ADMISSION_BLOCK = 'def _detect_admission(page) -> bool:\n    \'\'\'True if we\'re clearly past the lobby and in the call itself.\'\'\'\n    probe = r\'\'\'\n    (() => {\n      const norm = (value) => (value || \'\').toLocaleLowerCase()\n        .normalize(\'NFD\').replace(/[\\u0300-\\u036f]/g, \'\');\n      const visible = (el) => {\n        if (!el) return false;\n        const rect = el.getBoundingClientRect();\n        const style = window.getComputedStyle(el);\n        return !!(rect.width || rect.height) && style.visibility !== \'hidden\' && style.display !== \'none\';\n      };\n      for (const button of document.querySelectorAll(\'button,[role="button"]\')) {\n        const label = norm(`${button.innerText || \'\'} ${button.getAttribute(\'aria-label\') || \'\'}`);\n        if (visible(button) && /(leave call|salir de la llamada|abandonar llamada|finalizar llamada|colgar)/i.test(label)) {\n          return true;\n        }\n      }\n      if (window.__hermesMeetInstalled) {\n        const caps = document.querySelector(\n          \'[role="region"][aria-label*="aption" i], \' +\n          \'[role="region"][aria-label*="ubtítulos" i], \' +\n          \'[role="region"][aria-label*="ubtitulos" i], \' +\n          \'div[jsname="YSxPC"], div[jsname="tgaKEf"]\'\n        );\n        if (visible(caps)) return true;\n      }\n      for (const el of document.querySelectorAll(\'[aria-label],[role="button"],button\')) {\n        const label = norm(`${el.innerText || \'\'} ${el.getAttribute(\'aria-label\') || \'\'}`);\n        if (visible(el) && /(participants|participantes)/i.test(label)) return true;\n      }\n      return false;\n    })();\n    \'\'\'\n    try:\n        return bool(page.evaluate(probe))\n    except Exception:\n        return False\n'
MEET_BOT_OLD_DETECT_DENIED_BLOCK = 'def _detect_denied(page) -> bool:\n    """True when Meet is showing a \'you were denied\' / \'no one admitted\' page."""\n    probe = r"""\n    (() => {\n      const text = document.body ? document.body.innerText || \'\' : \'\';\n      // English only — matches what shows up when the host denies or\n      // removes a guest.\n      if (/You can\'t join this video call/i.test(text)) return true;\n      if (/You were removed from the meeting/i.test(text)) return true;\n      if (/No one responded to your request to join/i.test(text)) return true;\n      return false;\n    })();\n    """\n    try:\n        return bool(page.evaluate(probe))\n    except Exception:\n        return False\n'
MEET_BOT_NEW_DETECT_DENIED_BLOCK = "def _detect_denied(page) -> Optional[str]:\n    '''Return a specific blocked-admission reason, or None if still waiting.'''\n    probe = r'''\n    (() => {\n      const raw = document.body ? document.body.innerText || '' : '';\n      const text = raw.toLocaleLowerCase();\n      const flat = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '');\n      if (/you were removed from the meeting/i.test(raw)) return 'removed';\n      if (/te han quitado de la reunion|te han eliminado de la reunion/i.test(flat)) return 'removed';\n      if (/no one responded to your request to join/i.test(raw)) return 'no_response';\n      if (/nadie respondio a tu solicitud para unirte|no se ha respondido a tu solicitud/i.test(flat)) return 'no_response';\n      if (/you can't join this video call/i.test(raw)) return 'cannot_join';\n      if (/no puedes unirte a esta videollamada|no puedes unirte a esta llamada/i.test(flat)) return 'cannot_join';\n      if (/no se te ha admitido|request to join was denied|host denied/i.test(flat)) return 'denied';\n      if (/meeting has ended|call has ended/i.test(raw)) return 'meeting_ended';\n      if (/la reunion ha terminado|la llamada ha terminado/i.test(flat)) return 'meeting_ended';\n      if (/invalid meeting|meeting code.*invalid|expired meeting/i.test(raw)) return 'invalid_link';\n      if (/codigo.*reunion.*no valido|enlace.*no valido|reunion.*no existe/i.test(flat)) return 'invalid_link';\n      return null;\n    })();\n    '''\n    try:\n        reason = page.evaluate(probe)\n        return str(reason) if reason else None\n    except Exception:\n        return None\n"
MEET_BOT_OLD_CLICK_JOIN_BLOCK = 'def _click_join(page, state: _BotState) -> None:\n    """Click \'Join now\' or \'Ask to join\' if either button is visible.\n\n    Flags ``lobby_waiting`` when we hit the "waiting for host to admit you"\n    state so the agent can surface that in status.\n    """\n    for label in ("Join now", "Ask to join"):\n        try:\n            btn = page.get_by_role("button", name=label, exact=False).first\n            if btn.count() and btn.is_visible():\n                btn.click(timeout=3_000)\n                if label == "Ask to join":\n                    state.set(lobby_waiting=True)\n                break\n        except Exception:\n            continue\n'
MEET_BOT_NEW_CLICK_JOIN_BLOCK = '_JOIN_BUTTONS = (\n    "Join now",\n    "Ask to join",\n    "Unirse ahora",\n    "Solicitar unirse",\n    "Pedir unirse",\n    "Solicitar acceso",\n    "Unirte ahora",\n)\n_LOBBY_JOIN_LABELS = {"Ask to join", "Solicitar unirse", "Pedir unirse", "Solicitar acceso"}\n\n\ndef _click_join(page, state: _BotState) -> None:\n    \'\'\'Click a localized Join/Ask-to-join button if one is visible.\'\'\'\n    errors = []\n    for label in _JOIN_BUTTONS:\n        try:\n            btn = page.get_by_role("button", name=label, exact=False).first\n            if btn.count() and btn.is_visible():\n                btn.click(timeout=3_000)\n                state.set(\n                    join_button_found=True,\n                    join_button_clicked=label,\n                    join_button_error=None,\n                    join_attempted_at=time.time(),\n                    lobby_waiting=label in _LOBBY_JOIN_LABELS,\n                )\n                return\n        except Exception as e:\n            errors.append(f"{label}: {e}")\n\n    try:\n        clicked = page.evaluate(r\'\'\'\n        (labels) => {\n          const visible = (el) => {\n            const rect = el.getBoundingClientRect();\n            const style = window.getComputedStyle(el);\n            return !!(rect.width || rect.height) && style.visibility !== \'hidden\' && style.display !== \'none\';\n          };\n          const norm = (value) => (value || \'\').toLocaleLowerCase()\n            .normalize(\'NFD\').replace(/[\\u0300-\\u036f]/g, \'\');\n          const buttons = Array.from(document.querySelectorAll(\'button,[role="button"]\'));\n          for (const label of labels) {\n            const needle = norm(label);\n            const button = buttons.find((el) => {\n              if (!visible(el) || el.disabled || el.getAttribute(\'aria-disabled\') === \'true\') return false;\n              const text = norm(`${el.innerText || \'\'} ${el.getAttribute(\'aria-label\') || \'\'}`);\n              return text.includes(needle);\n            });\n            if (button) {\n              button.click();\n              return label;\n            }\n          }\n          return null;\n        }\n        \'\'\', list(_JOIN_BUTTONS))\n        if clicked:\n            label = str(clicked)\n            state.set(\n                join_button_found=True,\n                join_button_clicked=label,\n                join_button_error=None,\n                join_attempted_at=time.time(),\n                lobby_waiting=label in _LOBBY_JOIN_LABELS,\n            )\n            return\n    except Exception as e:\n        errors.append(f"dom fallback: {e}")\n\n    message = "join button not found"\n    if errors:\n        message += "; " + "; ".join(errors[-3:])\n    _store_debug_capture(state, _capture_debug(page, state.out_dir, "no_join_button"))\n    state.set(join_button_found=False, join_button_error=message)\n'


CLI_OLD_HELPER_BLOCK = '''def _auth_state_path() -> Path:
    return Path(get_hermes_home()) / "workspace" / "meetings" / "auth.json"


# ---------------------------------------------------------------------------
'''

CLI_NEW_HELPER_BLOCK = '''def _auth_state_path() -> Path:
    return Path(get_hermes_home()) / "workspace" / "meetings" / "auth.json"


def _meet_camofox_mode_enabled() -> bool:
    return bool(os.environ.get("CAMOFOX_URL", "").strip())


def _resolve_meet_cdp() -> tuple[str, str]:
    # Spartan Gate patch: Google Meet setup accepts Browserless CDP
    if _meet_camofox_mode_enabled():
        return "", "camofox"

    explicit = os.environ.get("HERMES_MEET_CDP_URL", "").strip()
    if explicit:
        return explicit, "custom"

    profile = os.environ.get("HERMES_MEET_CDP_PROFILE", "guest").strip().lower()
    browserless_profile = os.environ.get("BROWSERLESS_PROFILE", "main").strip().lower()
    if profile == "main" or (browserless_profile and profile == browserless_profile):
        return os.environ.get("BROWSER_CDP_MAIN_URL", "").strip(), "main"

    return os.environ.get("BROWSER_CDP_URL", "").strip(), "guest"


# ---------------------------------------------------------------------------
'''

CLI_OLD_SETUP_BLOCK = '''def _cmd_setup() -> int:
    import platform as _p

    print("google_meet preflight")
    print("---------------------")

    system = _p.system()
    system_ok = system in {"Linux", "Darwin"}
    print(f"  platform       : {system}  [{'ok' if system_ok else 'unsupported'}]")

    try:
        import playwright  # noqa: F401
        pw_ok = True
        pw_msg = "installed"
    except ImportError:
        pw_ok = False
        pw_msg = "NOT installed — run: pip install playwright"
    print(f"  playwright     : {pw_msg}")

    chromium_ok = False
    chromium_msg = "unknown"
    if pw_ok:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                try:
                    exe = p.chromium.executable_path
                    if exe and Path(exe).exists():
                        chromium_ok = True
                        chromium_msg = f"ok ({exe})"
                    else:
                        chromium_msg = (
                            "not installed — run: "
                            "python -m playwright install chromium"
                        )
                except Exception as e:
                    chromium_msg = f"probe failed: {e}"
        except Exception as e:
            chromium_msg = f"probe failed: {e}"
    print(f"  chromium       : {chromium_msg}")

    auth_path = _auth_state_path()
    auth_ok = auth_path.is_file()
    print(
        "  google auth    : "
        + (f"ok ({auth_path})" if auth_ok else "not saved — run: hermes meet auth")
    )

    print()
    all_ok = system_ok and pw_ok and chromium_ok
    if all_ok:
        print(
            "ready. Join a meeting:  "
            "hermes meet join https://meet.google.com/abc-defg-hij"
        )
    else:
        print("not ready yet — fix the items above.")
    return 0 if all_ok else 1


'''

CLI_NEW_SETUP_BLOCK = '''def _cmd_setup() -> int:
    import platform as _p

    print("google_meet preflight")
    print("---------------------")

    if _meet_camofox_mode_enabled():
        import requests
        camofox_url = os.environ.get("CAMOFOX_URL", "").rstrip("/")
        camofox_user = os.environ.get("CAMOFOX_USER_ID", "").strip() or "spartan-camofox-main"
        session_key = (
            os.environ.get("HERMES_MEET_CAMOFOX_SESSION_KEY", "").strip()
            or "meet"
        )
        headers = {}
        access_key = os.environ.get("CAMOFOX_ACCESS_KEY", "").strip()
        if access_key:
            headers["Authorization"] = f"Bearer {access_key}"
        camofox_ok = False
        camofox_msg = "unknown"
        try:
            response = requests.get(f"{camofox_url}/health", headers=headers, timeout=5)
            camofox_ok = response.ok
            camofox_msg = f"ok ({response.status_code})" if response.ok else f"status {response.status_code}"
        except Exception as e:
            camofox_msg = f"connect failed: {e}"
        print("  browser backend: camofox  [transcribe]")
        print(f"  camofox api    : {camofox_msg}")
        print(f"  camofox user   : {camofox_user}")
        print(f"  meet session   : {session_key}")
        print()
        if camofox_ok:
            print("ready. Join a meeting:  hermes meet join https://meet.google.com/abc-defg-hij --mode transcribe")
            return 0
        print("not ready yet - fix Camofox health/API access.")
        return 1

    system = _p.system()
    system_ok = system in ("Linux", "Darwin")
    print(f"  platform       : {system}  [{'ok' if system_ok else 'unsupported'}]")

    try:
        import playwright  # noqa: F401
        pw_ok = True
        pw_msg = "installed"
    except ImportError:
        pw_ok = False
        pw_msg = "NOT installed — run: pip install playwright"
    print(f"  playwright     : {pw_msg}")

    cdp_url, cdp_profile = _resolve_meet_cdp()
    cdp_ok = False
    cdp_msg = "not configured"
    chromium_ok = False
    chromium_msg = "unknown"
    google_signed_in = None
    if pw_ok and cdp_url:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(cdp_url)
                try:
                    contexts = len(browser.contexts)
                    if cdp_profile in {"main", "custom"} and browser.contexts:
                        sign_in_cookie_names = {
                            "SID",
                            "HSID",
                            "SSID",
                            "APISID",
                            "SAPISID",
                            "__Secure-1PSID",
                            "__Secure-3PSID",
                            "LSID",
                        }
                        cookies = browser.contexts[0].cookies([
                            "https://accounts.google.com",
                            "https://google.com",
                            "https://meet.google.com",
                        ])
                        google_signed_in = any(cookie.get("name") in sign_in_cookie_names for cookie in cookies)
                finally:
                    if cdp_profile != "main":
                        try:
                            browser.close()
                        except Exception:
                            pass
                cdp_ok = True
                cdp_msg = f"ok ({cdp_profile}, contexts={contexts})"
                chromium_ok = True
                chromium_msg = "not required — using external CDP"
        except Exception as e:
            cdp_msg = f"connect failed: {e}"

    if pw_ok and not cdp_ok:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                try:
                    exe = p.chromium.executable_path
                    if exe and Path(exe).exists():
                        chromium_ok = True
                        chromium_msg = f"ok ({exe})"
                    else:
                        chromium_msg = (
                            "not installed — run: "
                            "python -m playwright install chromium"
                        )
                except Exception as e:
                    chromium_msg = f"probe failed: {e}"
        except Exception as e:
            chromium_msg = f"probe failed: {e}"
    print(f"  browser cdp    : {cdp_msg}")
    print(f"  chromium       : {chromium_msg}")

    auth_path = _auth_state_path()
    auth_ok = auth_path.is_file()
    if cdp_ok and cdp_profile in {"main", "custom"}:
        browserless_profile = os.environ.get("BROWSERLESS_PROFILE", "main").strip() or "main"
        auth_msg = f"Browserless profile {browserless_profile}" if cdp_profile == "main" else "custom CDP profile"
        if google_signed_in is True:
            auth_msg += " (Google sign-in detected)"
        elif google_signed_in is False:
            auth_msg += (
                " (Google sign-in not detected - run on host: "
                "sg stop hermes && "
                f"sg-browserless-profile-chrome {browserless_profile} https://accounts.google.com; "
                "close Chrome after login; sg up -d hermes"
                ")"
            )
    elif auth_ok:
        auth_msg = f"ok ({auth_path})"
    else:
        auth_msg = "guest/no saved auth"
    print("  google auth    : " + auth_msg)

    print()
    all_ok = system_ok and pw_ok and (cdp_ok or chromium_ok) and google_signed_in is not False
    if all_ok:
        print(
            "ready. Join a meeting:  "
            "hermes meet join https://meet.google.com/abc-defg-hij"
        )
    else:
        print("not ready yet — fix the items above.")
    return 0 if all_ok else 1


'''

CLI_CAMOFOX_SETUP_OLD_BLOCK = '''    if _meet_camofox_mode_enabled():
        print("  browser backend: camofox  [unsupported for google_meet plugin]")
        print()
        print(_meet_cdp_required_message())
        return 1

'''

CLI_CAMOFOX_SETUP_BLOCK = '''    if _meet_camofox_mode_enabled():
        import requests
        camofox_url = os.environ.get("CAMOFOX_URL", "").rstrip("/")
        camofox_user = os.environ.get("CAMOFOX_USER_ID", "").strip() or "spartan-camofox-main"
        session_key = (
            os.environ.get("HERMES_MEET_CAMOFOX_SESSION_KEY", "").strip()
            or os.environ.get("CAMOFOX_SESSION_KEY", "").strip()
            or "meet"
        )
        headers = {}
        access_key = os.environ.get("CAMOFOX_ACCESS_KEY", "").strip()
        if access_key:
            headers["Authorization"] = f"Bearer {access_key}"
        camofox_ok = False
        camofox_msg = "unknown"
        try:
            response = requests.get(f"{camofox_url}/health", headers=headers, timeout=5)
            camofox_ok = response.ok
            camofox_msg = f"ok ({response.status_code})" if response.ok else f"status {response.status_code}"
        except Exception as e:
            camofox_msg = f"connect failed: {e}"
        print("  browser backend: camofox  [transcribe]")
        print(f"  camofox api    : {camofox_msg}")
        print(f"  camofox user   : {camofox_user}")
        print(f"  meet session   : {session_key}")
        print()
        if camofox_ok:
            print("ready. Join a meeting:  hermes meet join https://meet.google.com/abc-defg-hij --mode transcribe")
            return 0
        print("not ready yet - fix Camofox health/API access.")
        return 1

'''


def replace_once(source: str, old: str, new: str, target: Path) -> tuple[str, bool]:
    if old not in source:
        raise ValueError(f'anchor block not found in {target}')
    return source.replace(old, new, 1), True



def patch_meet_bot_ready_source(source: str, *, required: bool) -> str:
    if MEET_BOT_READY_MARKER in source:
        return source
    if MEET_BOT_LOCALE_MARKER not in source:
        if required:
            raise ValueError(f'anchor block not found in {MEET_BOT_TARGET}: localized join diagnostics')
        return source
    if MEET_BOT_READY_HELPER_ANCHOR not in source or MEET_BOT_READY_JOIN_CALL_BLOCK not in source:
        if required:
            raise ValueError(f'anchor block not found in {MEET_BOT_TARGET}: pre-join readiness wait')
        return source

    patched = source.replace(MEET_BOT_READY_HELPER_ANCHOR, MEET_BOT_READY_HELPER_BLOCK, 1)
    patched = patched.replace(MEET_BOT_READY_JOIN_CALL_BLOCK, MEET_BOT_READY_JOIN_CALL_REPLACEMENT, 1)
    return patched


def patch_meet_bot_locale_source(source: str, *, required: bool) -> str:
    if MEET_BOT_LOCALE_MARKER in source:
        return patch_meet_bot_ready_source(source, required=False)
    if MEET_BOT_OLD_TRY_GUEST_NAME_BLOCK not in source:
        if required:
            raise ValueError(f'anchor block not found in {MEET_BOT_TARGET}: _try_guest_name')
        return source

    replacements = [
        (MEET_BOT_STATE_FIELDS_ANCHOR, MEET_BOT_STATE_FIELDS_BLOCK),
        (MEET_BOT_STATUS_FIELDS_ANCHOR, MEET_BOT_STATUS_FIELDS_BLOCK),
        (MEET_BOT_JOIN_CALL_BLOCK, MEET_BOT_JOIN_CALL_REPLACEMENT),
        (MEET_BOT_DENIED_BLOCK, MEET_BOT_DENIED_REPLACEMENT),
        (MEET_BOT_FINAL_STATUS_BLOCK, MEET_BOT_FINAL_STATUS_REPLACEMENT),
        (MEET_BOT_OLD_TRY_GUEST_NAME_BLOCK, MEET_BOT_NEW_TRY_GUEST_NAME_BLOCK),
        (MEET_BOT_OLD_DETECT_ADMISSION_BLOCK, MEET_BOT_NEW_DETECT_ADMISSION_BLOCK),
        (MEET_BOT_OLD_DETECT_DENIED_BLOCK, MEET_BOT_NEW_DETECT_DENIED_BLOCK),
        (MEET_BOT_OLD_CLICK_JOIN_BLOCK, MEET_BOT_NEW_CLICK_JOIN_BLOCK),
    ]
    patched = source
    for old, new in replacements:
        patched, _ = replace_once(patched, old, new, MEET_BOT_TARGET)
    return patch_meet_bot_ready_source(patched, required=True)


def ensure_meet_bot_camofox_backend_source(source: str) -> str:
    patched = source
    if '_CamofoxMeetClient' not in patched:
        start = patched.find('SAY_PCM_FILENAME = "speaker.pcm"\n\n\n')
        end = patched.find('def _is_safe_meet_url', start)
        if start != -1 and end != -1:
            helper_without_anchor = MEET_BOT_HELPER_BLOCK.split('\ndef _is_safe_meet_url', 1)[0] + '\n\n'
            patched = patched[:start] + helper_without_anchor + patched[end:]

    patched = patched.replace(
        '    _raise_meet_cdp_required_in_camofox_mode()\n    cdp_url, cdp_profile = _resolve_meet_cdp()\n',
        '    cdp_url, cdp_profile = _resolve_meet_cdp()\n',
        1,
    )
    patched = patched.replace(
        '            os.environ.get("HERMES_MEET_CAMOFOX_SESSION_KEY", "").strip()\n'
        '            or os.environ.get("CAMOFOX_SESSION_KEY", "").strip()\n'
        '            or "meet"\n',
        '            os.environ.get("HERMES_MEET_CAMOFOX_SESSION_KEY", "").strip()\n'
        '            or "meet"\n',
    )

    if MEET_BOT_REALTIME_REPLACEMENT not in patched and MEET_BOT_REALTIME_BLOCK in patched:
        patched = patched.replace(MEET_BOT_REALTIME_BLOCK, MEET_BOT_REALTIME_REPLACEMENT, 1)
    if MEET_BOT_NEW_IMPORT_BLOCK not in patched and MEET_BOT_OLD_IMPORT_BLOCK in patched:
        patched = patched.replace(MEET_BOT_OLD_IMPORT_BLOCK, MEET_BOT_NEW_IMPORT_BLOCK, 1)
    return patched


def patch_meet_bot_source(source: str) -> str:
    if MEET_BOT_MARKER in source:
        patched = ensure_meet_bot_camofox_backend_source(source)
        legacy = 'if cdp_profile != "main" and not cdp_shared_context:'
        if legacy in patched:
            patched = patched.replace(legacy, 'if cdp_profile != "main":', 1)
        profile_legacy = '    profile = os.environ.get("HERMES_MEET_CDP_PROFILE", "guest").strip().lower()\n    if profile == "main":\n        return os.environ.get("BROWSER_CDP_MAIN_URL", "").strip(), "main"\n'
        profile_current = '    profile = os.environ.get("HERMES_MEET_CDP_PROFILE", "guest").strip().lower()\n    browserless_profile = os.environ.get("BROWSERLESS_PROFILE", "main").strip().lower()\n    if profile == "main" or (browserless_profile and profile == browserless_profile):\n        return os.environ.get("BROWSER_CDP_MAIN_URL", "").strip(), "main"\n'
        if profile_legacy in patched:
            patched = patched.replace(profile_legacy, profile_current, 1)
        return patch_meet_bot_locale_source(patched, required=False)

    patched, _ = replace_once(source, MEET_BOT_HELPER_ANCHOR, MEET_BOT_HELPER_BLOCK, MEET_BOT_TARGET)
    if MEET_BOT_REALTIME_BLOCK in patched:
        patched = patched.replace(MEET_BOT_REALTIME_BLOCK, MEET_BOT_REALTIME_REPLACEMENT, 1)
    if MEET_BOT_OLD_IMPORT_BLOCK in patched:
        patched = patched.replace(MEET_BOT_OLD_IMPORT_BLOCK, MEET_BOT_NEW_IMPORT_BLOCK, 1)
    patched, _ = replace_once(patched, MEET_BOT_OLD_LAUNCH_BLOCK, MEET_BOT_NEW_LAUNCH_BLOCK, MEET_BOT_TARGET)
    patched, _ = replace_once(patched, MEET_BOT_OLD_TEARDOWN_BLOCK, MEET_BOT_NEW_TEARDOWN_BLOCK, MEET_BOT_TARGET)
    return patch_meet_bot_locale_source(patched, required=True)


def ensure_cli_camofox_backend_source(source: str) -> str:
    patched = source
    if 'def _meet_camofox_mode_enabled() -> bool:' not in patched:
        patched = patched.replace(
            'def _resolve_meet_cdp() -> tuple[str, str]:\n',
            'def _meet_camofox_mode_enabled() -> bool:\n'
            '    return bool(os.environ.get("CAMOFOX_URL", "").strip())\n'
            '\n'
            '\n'
            'def _resolve_meet_cdp() -> tuple[str, str]:\n',
            1,
        )

    if 'def _meet_cdp_required_message() -> str:' in patched:
        start = patched.find('def _meet_cdp_required_message() -> str:')
        end = patched.find('\ndef _resolve_meet_cdp() -> tuple[str, str]:', start)
        if start != -1 and end != -1:
            patched = patched[:start] + patched[end + 1:]

    if 'if _meet_camofox_mode_enabled():\n        return "", "camofox"' not in patched:
        patched = patched.replace(
            'def _resolve_meet_cdp() -> tuple[str, str]:\n    # Spartan Gate patch: Google Meet setup accepts Browserless CDP\n',
            'def _resolve_meet_cdp() -> tuple[str, str]:\n'
            '    # Spartan Gate patch: Google Meet setup accepts Browserless CDP\n'
            '    if _meet_camofox_mode_enabled():\n'
            '        return "", "camofox"\n'
            '\n',
            1,
        )
    if 'if _meet_camofox_mode_enabled():\n        return "", "camofox"' not in patched:
        patched = patched.replace(
            'def _resolve_meet_cdp() -> tuple[str, str]:\n',
            'def _resolve_meet_cdp() -> tuple[str, str]:\n'
            '    if _meet_camofox_mode_enabled():\n'
            '        return "", "camofox"\n'
            '\n',
            1,
        )

    patched = patched.replace(
        '            os.environ.get("HERMES_MEET_CAMOFOX_SESSION_KEY", "").strip()\n'
        '            or os.environ.get("CAMOFOX_SESSION_KEY", "").strip()\n'
        '            or "meet"\n',
        '            os.environ.get("HERMES_MEET_CAMOFOX_SESSION_KEY", "").strip()\n'
        '            or "meet"\n',
    )

    if CLI_CAMOFOX_SETUP_OLD_BLOCK in patched:
        patched = patched.replace(CLI_CAMOFOX_SETUP_OLD_BLOCK, CLI_CAMOFOX_SETUP_BLOCK, 1)
    elif 'browser backend: camofox  [transcribe]' not in patched:
        patched = patched.replace(
            '    print("google_meet preflight")\n    print("---------------------")\n\n',
            '    print("google_meet preflight")\n'
            '    print("---------------------")\n'
            '\n'
            + CLI_CAMOFOX_SETUP_BLOCK,
            1,
        )
    return patched


def patch_cli_source(source: str) -> str:
    if CLI_MARKER in source:
        profile_legacy = '    profile = os.environ.get("HERMES_MEET_CDP_PROFILE", "guest").strip().lower()\n    if profile == "main":\n        return os.environ.get("BROWSER_CDP_MAIN_URL", "").strip(), "main"\n'
        profile_current = '    profile = os.environ.get("HERMES_MEET_CDP_PROFILE", "guest").strip().lower()\n    browserless_profile = os.environ.get("BROWSERLESS_PROFILE", "main").strip().lower()\n    if profile == "main" or (browserless_profile and profile == browserless_profile):\n        return os.environ.get("BROWSER_CDP_MAIN_URL", "").strip(), "main"\n'
        patched = ensure_cli_camofox_backend_source(source.replace(profile_legacy, profile_current, 1))
        patched = patched.replace('auth_msg = "Browserless profile main"', 'browserless_profile = os.environ.get("BROWSERLESS_PROFILE", "main").strip() or "main"\n        auth_msg = f"Browserless profile {browserless_profile}"', 1)
        patched = patched.replace('if cdp_profile == "main" and browser.contexts:', 'if cdp_profile in {"main", "custom"} and browser.contexts:', 1)
        patched = patched.replace(
            '    if cdp_ok and cdp_profile == "main":\n        browserless_profile = os.environ.get("BROWSERLESS_PROFILE", "main").strip() or "main"\n        auth_msg = f"Browserless profile {browserless_profile}"',
            '    if cdp_ok and cdp_profile in {"main", "custom"}:\n        browserless_profile = os.environ.get("BROWSERLESS_PROFILE", "main").strip() or "main"\n        auth_msg = f"Browserless profile {browserless_profile}" if cdp_profile == "main" else "custom CDP profile"',
            1,
        )
        patched = patched.replace(
            '        elif google_signed_in is False:\n            auth_msg += (\n                " (Google sign-in not detected - run: "\n                f"sg-browserless-profile-live {browserless_profile} https://accounts.google.com"\n                ")"\n            )\n',
            '        elif google_signed_in is False:\n            auth_msg += (\n                " (Google sign-in not detected - run on host: "\n                "sg stop hermes && "\n                f"sg-browserless-profile-chrome {browserless_profile} https://accounts.google.com; "\n                "close Chrome after login; sg up -d hermes"\n                ")"\n            )\n',
            1,
        )
        if 'google_signed_in = None' not in patched:
            patched = patched.replace(
                '    chromium_ok = False\n    chromium_msg = "unknown"\n    if pw_ok and cdp_url:\n',
                '    chromium_ok = False\n    chromium_msg = "unknown"\n    google_signed_in = None\n    if pw_ok and cdp_url:\n',
                1,
            )
            patched = patched.replace(
                '                browser = p.chromium.connect_over_cdp(cdp_url)\n                try:\n                    contexts = len(browser.contexts)\n                finally:\n',
                '                browser = p.chromium.connect_over_cdp(cdp_url)\n                try:\n                    contexts = len(browser.contexts)\n                    if cdp_profile in {"main", "custom"} and browser.contexts:\n                        sign_in_cookie_names = {\n                            "SID",\n                            "HSID",\n                            "SSID",\n                            "APISID",\n                            "SAPISID",\n                            "__Secure-1PSID",\n                            "__Secure-3PSID",\n                            "LSID",\n                        }\n                        cookies = browser.contexts[0].cookies([\n                            "https://accounts.google.com",\n                            "https://google.com",\n                            "https://meet.google.com",\n                        ])\n                        google_signed_in = any(cookie.get("name") in sign_in_cookie_names for cookie in cookies)\n                finally:\n',
                1,
            )
            patched = patched.replace(
                '        auth_msg = f"Browserless profile {browserless_profile}"\n',
                '        auth_msg = f"Browserless profile {browserless_profile}"\n        if google_signed_in is True:\n            auth_msg += " (Google sign-in detected)"\n        elif google_signed_in is False:\n            auth_msg += (\n                " (Google sign-in not detected - run on host: "\n                "sg stop hermes && "\n                f"sg-browserless-profile-chrome {browserless_profile} https://accounts.google.com; "\n                "close Chrome after login; sg up -d hermes"\n                ")"\n            )\n',
                1,
            )
            patched = patched.replace(
                '    all_ok = system_ok and pw_ok and (cdp_ok or chromium_ok)\n',
                '    all_ok = system_ok and pw_ok and (cdp_ok or chromium_ok) and google_signed_in is not False\n',
                1,
            )
        return patched

    patched, _ = replace_once(source, CLI_OLD_HELPER_BLOCK, CLI_NEW_HELPER_BLOCK, CLI_TARGET)
    patched, _ = replace_once(patched, CLI_OLD_SETUP_BLOCK, CLI_NEW_SETUP_BLOCK, CLI_TARGET)
    return patched


def patch_file(path: Path, patcher, label: str) -> str:
    if not path.exists():
        return f'Skip: {path} missing'

    source = path.read_text(encoding='utf-8')
    try:
        patched = patcher(source)
    except ValueError as exc:
        raise ValueError(f'{label}: {exc}') from exc

    if patched == source:
        return f'Already patched: {path}'

    path.write_text(patched, encoding='utf-8')
    return f'Patched: {path} — Google Meet backend selection enabled'


def main() -> None:
    try:
        print(patch_file(MEET_BOT_TARGET, patch_meet_bot_source, 'meet_bot'))
        print(patch_file(CLI_TARGET, patch_cli_source, 'cli'))
    except ValueError as exc:
        print(f'FATAL: {exc} — upstream may have changed', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
