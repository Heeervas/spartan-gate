#!/usr/bin/env python3
"""Runtime patcher: make Google Meet transcript capture robust in real calls."""

from __future__ import annotations

import re
import sys
from pathlib import Path

MEET_BOT_TARGET = Path('/opt/hermes/plugins/google_meet/meet_bot.py')
CLI_TARGET = Path('/opt/hermes/plugins/google_meet/cli.py')
PROCESS_MANAGER_TARGET = Path('/opt/hermes/plugins/google_meet/process_manager.py')
INIT_TARGET = Path('/opt/hermes/plugins/google_meet/__init__.py')

MEET_BOT_MARKER = '# Spartan Gate patch: Google Meet transcript capture and muted join'
CLI_MARKER = '# Spartan Gate patch: Google Meet leave alias'
PROCESS_MANAGER_MARKER = '# Spartan Gate patch: Google Meet stop permission diagnostics'
CLI_SETUP_PERMISSION_MARKER = '# Spartan Gate patch: Google Meet setup auth permission guard'
CLI_JOIN_AUTH_MARKER = '# Spartan Gate patch: Google Meet join auth permission guard'
INIT_MARKER = '# Spartan Gate patch: Google Meet lazy CLI import'

CAPTION_AND_MEDIA_BLOCK = r'''
# Spartan Gate patch: Google Meet transcript capture and muted join
_CAPTION_OBSERVER_JS = r"""
(() => {
  if (!window.__hermesMeetQueue) window.__hermesMeetQueue = [];
  if (!window.__hermesMeetSeenCaptions) window.__hermesMeetSeenCaptions = new Set();
  if (!window.__hermesMeetDebug) window.__hermesMeetDebug = {};

  const captionSelector = [
    '[role="region"][aria-label*="caption" i]',
    '[role="region"][aria-label*="captions" i]',
    '[role="region"][aria-label*="subt" i]',
    '[aria-label*="caption" i]',
    '[aria-label*="subt" i]',
    '[aria-live="polite"]',
    '[aria-live="assertive"]',
    'div[jsname="dsyhDe"]',
    'div[jsname="YSxPC"]',
    'div[jsname="tgaKEf"]',
    'div.CNusmb',
    'div.TBMuR'
  ].join(', ');

  const norm = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return !!(rect.width || rect.height) && style.visibility !== 'hidden' && style.display !== 'none';
  };

  function pushEntry(speaker, text) {
    const clean = norm(text);
    if (!clean || clean.length < 2) return;
    if (/^(more_vert|expand_more|mic|videocam|present_to_all)$/i.test(clean)) return;
    const who = norm(speaker);
    const key = `${who}\n${clean}`;
    if (window.__hermesMeetSeenCaptions.has(key)) return;
    window.__hermesMeetSeenCaptions.add(key);
    window.__hermesMeetQueue.push({ts: Date.now(), speaker: who, text: clean});
    window.__hermesMeetDebug.lastSample = clean;
    window.__hermesMeetDebug.lastCaptionAt = Date.now();
  }

  function scan(root) {
    if (!root || !visible(root)) return false;
    const rows = root.querySelectorAll('div[jsname="dsyhDe"], div.CNusmb, div.TBMuR');
    let found = false;
    if (rows.length) {
      rows.forEach((row) => {
        if (!visible(row)) return;
        const spkEl = row.querySelector('div.KcIKyf, div.zs7s8d, span[jsname="YSxPC"]');
        const txtEl = row.querySelector('div.bh44bd, span[jsname="tgaKEf"], div.iTTPOb');
        const text = txtEl ? txtEl.innerText : row.innerText;
        if (norm(text)) {
          pushEntry(spkEl ? spkEl.innerText : '', text);
          found = true;
        }
      });
      return found;
    }

    const lines = (root.innerText || '').split('\n').map(norm).filter(Boolean);
    for (const line of lines.slice(-8)) {
      pushEntry('', line);
      found = true;
    }
    return found;
  }

  function candidateRegions() {
    return Array.from(document.querySelectorAll(captionSelector)).filter(visible).slice(0, 60);
  }

  function scanAll() {
    const regions = candidateRegions();
    window.__hermesMeetDebug.regionFound = regions.length > 0;
    window.__hermesMeetDebug.regionCount = regions.length;
    window.__hermesMeetDebug.lastScanAt = Date.now();
    let captured = false;
    for (const region of regions) {
      captured = scan(region) || captured;
    }
    return {regionFound: regions.length > 0, captured, sample: window.__hermesMeetDebug.lastSample || ''};
  }

  function attachObservers() {
    if (window.__hermesMeetObserversAttached) return true;
    const body = document.body || document.documentElement;
    if (!body) return false;
    const obs = new MutationObserver(() => scanAll());
    obs.observe(body, {childList: true, subtree: true, characterData: true});
    window.__hermesMeetObserversAttached = true;
    window.__hermesMeetInstalled = true;
    window.__hermesMeetDebug.observerReady = true;
    return true;
  }

  window.__hermesMeetScanCaptions = scanAll;
  window.__hermesMeetDrain = () => {
    scanAll();
    const out = window.__hermesMeetQueue.slice();
    window.__hermesMeetQueue = [];
    return out;
  };

  attachObservers();
  scanAll();
  if (!window.__hermesMeetPoller) {
    window.__hermesMeetPoller = setInterval(scanAll, 1000);
  }
  return true;
})();
"""


def _enable_captions_js() -> str:
    """Return JS that turns on Meet captions without toggling them off."""
    return r"""
    (() => {
      const norm = (value) => (value || '').toLocaleLowerCase()
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .replace(/\s+/g, ' ').trim();
      const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return !!(rect.width || rect.height) && style.visibility !== 'hidden' && style.display !== 'none';
      };
      if (typeof window.__hermesMeetScanCaptions === 'function') {
        window.__hermesMeetScanCaptions();
      }
      const before = window.__hermesMeetDebug || {};
      let clicked = false;
      if (!before.regionFound && !window.__hermesMeetCaptionClickAttempted) {
        const buttons = Array.from(document.querySelectorAll('button,[role="button"]'));
        const turnOn = /(turn on captions|show captions|activar subtitulos|mostrar subtitulos)/i;
        const turnOff = /(turn off captions|hide captions|desactivar subtitulos|ocultar subtitulos)/i;
        for (const button of buttons) {
          if (!visible(button) || button.disabled || button.getAttribute('aria-disabled') === 'true') continue;
          const label = norm(`${button.innerText || ''} ${button.getAttribute('aria-label') || ''}`);
          if (turnOn.test(label) && !turnOff.test(label)) {
            button.click();
            clicked = true;
            window.__hermesMeetCaptionClickAttempted = true;
            break;
          }
        }
      }
      if (typeof window.__hermesMeetScanCaptions === 'function') {
        window.__hermesMeetScanCaptions();
      }
      const afterClick = window.__hermesMeetDebug || {};
      if (!afterClick.regionFound && !clicked && !window.__hermesMeetCaptionShortcutAttempted) {
        window.__hermesMeetCaptionShortcutAttempted = true;
        const ev = new KeyboardEvent('keydown', {
          key: 'c', code: 'KeyC', keyCode: 67, which: 67, bubbles: true,
        });
        document.body.dispatchEvent(ev);
      }
      if (typeof window.__hermesMeetScanCaptions === 'function') {
        window.__hermesMeetScanCaptions();
      }
      const debug = window.__hermesMeetDebug || {};
      return {
        clicked,
        regionFound: !!debug.regionFound,
        observerReady: !!debug.observerReady,
        sample: debug.lastSample || '',
      };
    })();
    """


def _ensure_caption_capture(page, state: "_BotState") -> None:
    try:
        page.evaluate(_CAPTION_OBSERVER_JS)
        state.set(caption_observer_ready=True)
    except Exception as e:
        state.set(caption_observer_ready=False, caption_enable_error=f"caption observer install failed: {e}")
        return

    try:
        result = page.evaluate(_enable_captions_js())
        updates = {"captions_enabled_attempted": True, "last_caption_probe_at": time.time()}
        if isinstance(result, dict):
            updates["caption_region_found"] = bool(result.get("regionFound"))
            updates["caption_observer_ready"] = bool(result.get("observerReady"))
            sample = str(result.get("sample") or "")[:500]
            if sample:
                updates["last_caption_text_sample"] = sample
        state.set(**updates)
    except Exception as e:
        try:
            page.keyboard.press("c")
            state.set(captions_enabled_attempted=True, last_caption_probe_at=time.time())
        except Exception:
            state.set(caption_enable_error=f"caption enable failed: {e}")


def _mute_prejoin_media(page, state: "_BotState") -> None:
    try:
        result = page.evaluate(r"""
        () => {
          const norm = (value) => (value || '').toLocaleLowerCase()
            .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .replace(/\s+/g, ' ').trim();
          const visible = (el) => {
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return !!(rect.width || rect.height) && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const buttons = Array.from(document.querySelectorAll('button,[role="button"]')).filter(visible);
          const body = norm(document.body ? document.body.innerText || '' : '');
          const out = {micMuted: false, cameraMuted: false, micClicked: false, cameraClicked: false};

          function clickFirst(activeRe, inactiveRe, fieldClicked, fieldMuted) {
            for (const button of buttons) {
              if (button.disabled || button.getAttribute('aria-disabled') === 'true') continue;
              const label = norm(`${button.innerText || ''} ${button.getAttribute('aria-label') || ''}`);
              if (inactiveRe.test(label)) {
                out[fieldMuted] = true;
                return;
              }
              if (activeRe.test(label)) {
                button.click();
                out[fieldClicked] = true;
                out[fieldMuted] = true;
                return;
              }
            }
          }

          clickFirst(
            /(turn off microphone|mute microphone|desactivar microfono|silenciar microfono|microfono activado)/i,
            /(turn on microphone|unmute microphone|activar microfono|microfono desactivado|microfono silenciado)/i,
            'micClicked', 'micMuted'
          );
          clickFirst(
            /(turn off camera|disable camera|desactivar camara|camara activada)/i,
            /(turn on camera|enable camera|activar camara|camara desactivada|camara no encontrada|sin camara)/i,
            'cameraClicked', 'cameraMuted'
          );

          out.micActiveText = /tu microfono esta activado|your microphone is on/i.test(body);
          out.cameraActiveText = /tu camara esta activada|your camera is on/i.test(body);
          return out;
        }
        """)
        updates = {}
        if isinstance(result, dict):
            updates["mic_muted_before_join"] = bool(result.get("micMuted"))
            updates["camera_muted_before_join"] = bool(result.get("cameraMuted"))
            if result.get("micActiveText") and not result.get("micMuted"):
                try:
                    page.keyboard.press("Control+D")
                    updates["mic_muted_before_join"] = True
                except Exception:
                    pass
            if result.get("cameraActiveText") and not result.get("cameraMuted"):
                try:
                    page.keyboard.press("Control+E")
                    updates["camera_muted_before_join"] = True
                except Exception:
                    pass
        state.set(**updates)
    except Exception as e:
        state.set(media_mute_error=str(e))

'''

ADMISSION_BLOCK = r"""def _detect_admission(page) -> bool:
    '''True if we're clearly past the lobby and in the call itself.'''
    probe = r'''
    (() => {
      const norm = (value) => (value || '').toLocaleLowerCase()
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .replace(/\s+/g, ' ').trim();
      const visible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return !!(rect.width || rect.height) && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const joinRe = /(join now|ask to join|unirse ahora|unirme ahora|solicitar unirse|pedir unirse|solicitar acceso)/i;
      const inCallRe = /(leave call|hang up|salir de la llamada|abandonar llamada|finalizar llamada|colgar|participantes|participants|personas en la llamada|people in this call|chat con todos|raise hand|levantar la mano)/i;
      for (const el of document.querySelectorAll('button,[role=\"button\"],[aria-label]')) {
        const label = norm(`${el.innerText || ''} ${el.getAttribute('aria-label') || ''}`);
        if (visible(el) && inCallRe.test(label) && !joinRe.test(label)) return true;
      }
      if (window.__hermesMeetDebug && window.__hermesMeetDebug.regionFound) return true;
      const caps = document.querySelector(
        '[role=\"region\"][aria-label*=\"caption\" i], ' +
        '[role=\"region\"][aria-label*=\"subt\" i], ' +
        '[aria-live=\"polite\"], [aria-live=\"assertive\"], ' +
        'div[jsname=\"dsyhDe\"], div[jsname=\"YSxPC\"], div[jsname=\"tgaKEf\"]'
      );
      if (visible(caps)) return true;
      const body = norm(document.body ? document.body.innerText || '' : '');
      if (!joinRe.test(body) && /(participantes|participants|personas en la llamada|people in this call)/i.test(body)) return true;
      return false;
    })();
    '''
    try:
        return bool(page.evaluate(probe))
    except Exception:
        return False

"""

def _replace_regex(source: str, pattern: str, replacement: str, label: str) -> str:
    patched, count = re.subn(pattern, lambda _match: replacement, source, count=1, flags=re.S)
    if count != 1:
        raise ValueError(f'anchor block not found in {MEET_BOT_TARGET}: {label}')
    return migrate_meet_bot_source(patched)


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    if old not in source:
        raise ValueError(f'anchor block not found in {MEET_BOT_TARGET}: {label}')
    return source.replace(old, new, 1)


def _replace_many(source: str, replacements: list[tuple[str, str, str]]) -> str:
    missing = [label for old, _new, label in replacements if old not in source]
    if missing:
        raise ValueError(
            f'anchor block not found in {MEET_BOT_TARGET}: {", ".join(missing)}'
        )
    patched = source
    for old, new, _label in replacements:
        patched = patched.replace(old, new, 1)
    return patched


def migrate_meet_bot_source(source: str) -> str:
    patched = source
    old_turn_on = "        const turnOn = /(turn on captions|show captions|activar subtitulos|mostrar subtitulos|subtitulos|captions)/i;\n"
    if old_turn_on in patched:
        patched = _replace_once(
            patched,
            old_turn_on,
            "        const turnOn = /(turn on captions|show captions|activar subtitulos|mostrar subtitulos)/i;\n",
            'caption activation labels',
        )
    old_click_guard = (
        "      if (!before.regionFound) {\n"
        "        const buttons = Array.from(document.querySelectorAll('button,[role=\"button\"]'));\n"
    )
    old_click_marker = "            button.click();\n            clicked = true;\n"
    new_click_guard = (
        "      if (!before.regionFound && !window.__hermesMeetCaptionClickAttempted) {\n"
    )
    if old_click_guard in patched:
        patched = _replace_many(patched, [
            (
                old_click_guard,
                new_click_guard
                + "        const buttons = Array.from(document.querySelectorAll('button,[role=\"button\"]'));\n",
                'caption click guard',
            ),
            (
                old_click_marker,
                "            button.click();\n            clicked = true;\n            window.__hermesMeetCaptionClickAttempted = true;\n",
                'caption click marker',
            ),
        ])
    elif (
        new_click_guard in patched
        and "window.__hermesMeetCaptionClickAttempted = true;" not in patched
    ):
        patched = _replace_once(
            patched,
            old_click_marker,
            old_click_marker + "            window.__hermesMeetCaptionClickAttempted = true;\n",
            'caption click marker',
        )
    if "window.__hermesMeetCaptionShortcutAttempted" not in patched:
        patched = patched.replace(
            "      if (!afterClick.regionFound && !clicked) {\n        const ev = new KeyboardEvent('keydown', {\n",
            "      if (!afterClick.regionFound && !clicked && !window.__hermesMeetCaptionShortcutAttempted) {\n        window.__hermesMeetCaptionShortcutAttempted = true;\n        const ev = new KeyboardEvent('keydown', {\n",
            1,
        )
    return patched


def patch_meet_bot_source(source: str) -> str:
    if MEET_BOT_MARKER in source:
        return migrate_meet_bot_source(source)
    patched = source

    patched = _replace_once(
        patched,
        '        self.join_button_error: Optional[str] = None\n',
        '        self.join_button_error: Optional[str] = None\n'
        '        self.mic_muted_before_join = False\n'
        '        self.camera_muted_before_join = False\n'
        '        self.media_mute_error: Optional[str] = None\n'
        '        self.admission_detected = False\n'
        '        self.caption_region_found = False\n'
        '        self.caption_observer_ready = False\n'
        '        self.last_caption_probe_at: Optional[float] = None\n'
        '        self.last_caption_text_sample: Optional[str] = None\n'
        '        self.caption_enable_error: Optional[str] = None\n',
        'state fields',
    )
    patched = _replace_once(
        patched,
        '            "joinButtonError": self.join_button_error,\n',
        '            "joinButtonError": self.join_button_error,\n'
        '            "micMutedBeforeJoin": self.mic_muted_before_join,\n'
        '            "cameraMutedBeforeJoin": self.camera_muted_before_join,\n'
        '            "mediaMuteError": self.media_mute_error,\n'
        '            "admissionDetected": self.admission_detected,\n'
        '            "captionRegionFound": self.caption_region_found,\n'
        '            "captionObserverReady": self.caption_observer_ready,\n'
        '            "lastCaptionProbeAt": self.last_caption_probe_at,\n'
        '            "lastCaptionTextSample": self.last_caption_text_sample,\n'
        '            "captionEnableError": self.caption_enable_error,\n',
        'status fields',
    )

    patched = _replace_regex(
        patched,
        r'# JavaScript injected into the Meet tab to observe captions\..*?def _start_realtime_speaker\(',
        CAPTION_AND_MEDIA_BLOCK + 'def _start_realtime_speaker(',
        'caption observer/media helper',
    )
    patched = _replace_regex(
        patched,
        r'def _detect_admission\(page\) -> bool:\n.*?\ndef _detect_denied\(',
        ADMISSION_BLOCK + 'def _detect_denied(',
        'admission detection',
    )

    patched = patched.replace(
        '    "Unirse ahora",\n    "Solicitar unirse",\n',
        '    "Unirse ahora",\n    "Unirme ahora",\n    "Unirse ahora sin cámara",\n    "Solicitar unirse",\n    "Solicitar unirme",\n',
        1,
    )
    patched = patched.replace(
        '    "Unirte ahora",\n)\n',
        '    "Unirte ahora",\n    "Join without camera",\n)\n',
        1,
    )

    patched = _replace_once(
        patched,
        '                if not _detect_admission(page) and _wait_for_join_controls(page, state, timeout_ms=60_000, require_join_button=True):\n                    _click_join(page, state)\n',
        '                if not _detect_admission(page) and _wait_for_join_controls(page, state, timeout_ms=60_000, require_join_button=True):\n                    _mute_prejoin_media(page, state)\n                    _click_join(page, state)\n',
        'pre-join mute call',
    )
    patched = _replace_once(
        patched,
        '            # Install caption observer and attempt to enable captions.\n            try:\n                page.evaluate(_enable_captions_js())\n                state.set(captions_enabled_attempted=True)\n            except Exception:\n                pass\n            try:\n                page.evaluate(_CAPTION_OBSERVER_JS)\n            except Exception as e:\n                state.set(error=f"caption observer install failed: {e}")\n\n            # Note: in_call=False until admission is confirmed (we detect\n',
        '            # Install caption observer and attempt to enable captions.\n            _ensure_caption_capture(page, state)\n\n            # Note: in_call=False until admission is confirmed (we detect\n',
        'caption install block',
    )
    patched = _replace_once(
        patched,
        '            last_admission_check = 0.0\n            while not stop_flag["stop"]:\n',
        '            last_admission_check = 0.0\n            last_caption_capture_check = 0.0\n            while not stop_flag["stop"]:\n',
        'caption loop timer',
    )
    patched = _replace_once(
        patched,
        '                    if admitted:\n                        state.set(\n                            in_call=True,\n                            lobby_waiting=False,\n                            joined_at=now,\n                        )\n',
        '                    if admitted:\n                        _mute_prejoin_media(page, state)\n                        _ensure_caption_capture(page, state)\n                        state.set(\n                            in_call=True,\n                            lobby_waiting=False,\n                            admission_detected=True,\n                            joined_at=now,\n                        )\n',
        'admission state block',
    )
    patched = _replace_once(
        patched,
        '                try:\n                    queued = page.evaluate("window.__hermesMeetDrain && window.__hermesMeetDrain()")\n',
        '                if (now - last_caption_capture_check) > 3.0:\n                    last_caption_capture_check = now\n                    _ensure_caption_capture(page, state)\n\n                try:\n                    debug = page.evaluate("window.__hermesMeetDebug || {}")\n                    if isinstance(debug, dict):\n                        state.set(\n                            caption_region_found=bool(debug.get("regionFound")),\n                            caption_observer_ready=bool(debug.get("observerReady")),\n                            last_caption_probe_at=now,\n                            last_caption_text_sample=str(debug.get("lastSample") or "")[:500] or state.last_caption_text_sample,\n                        )\n                    queued = page.evaluate("window.__hermesMeetDrain && window.__hermesMeetDrain()")\n',
        'caption drain diagnostics',
    )
    return patched


def patch_cli_source(source: str) -> str:
    patched = source
    if CLI_MARKER not in patched:
        patched = _replace_many(patched, [
            (
                '    subs.add_parser("stop", help="Leave the current meeting")\n',
                '    subs.add_parser("stop", help="Leave the current meeting")\n'
                '    subs.add_parser("leave", help="Alias for stop")\n'
                f'    {CLI_MARKER}\n',
                'leave parser',
            ),
            (
                '        print("usage: hermes meet {setup,auth,join,status,transcript,say,stop,node}")\n',
                '        print("usage: hermes meet {setup,auth,join,status,transcript,say,stop,leave,node}")\n',
                'leave usage',
            ),
            (
                '    if sub == "stop":\n        return _cmd_stop()\n',
                '    if sub in ("stop", "leave"):\n        return _cmd_stop()\n',
                'leave dispatch',
            ),
        ])

    if CLI_SETUP_PERMISSION_MARKER not in patched:
        patched = patched.replace(
            '    auth_path = _auth_state_path()\n    auth_ok = auth_path.is_file()\n',
            '    auth_path = _auth_state_path()\n'
            f'    {CLI_SETUP_PERMISSION_MARKER}\n'
            '    auth_error = None\n'
            '    try:\n'
            '        auth_ok = auth_path.is_file()\n'
            '    except PermissionError as e:\n'
            '        auth_ok = False\n'
            '        auth_error = f"permission denied reading {auth_path}: {e}"\n',
            1,
        )
        patched = patched.replace(
            '    else:\n        auth_msg = "guest/no saved auth"\n',
            '    else:\n'
            '        auth_msg = f"guest/no saved auth ({auth_error})" if auth_error else "guest/no saved auth"\n',
            1,
        )

    if CLI_JOIN_AUTH_MARKER not in patched:
        patched = patched.replace(
            '    auth = _auth_state_path()\n    res = pm.start(\n',
            '    auth = _auth_state_path()\n'
            f'    {CLI_JOIN_AUTH_MARKER}\n'
            '    try:\n'
            '        auth_state = str(auth) if auth.is_file() else None\n'
            '    except PermissionError:\n'
            '        auth_state = None\n'
            '    res = pm.start(\n',
            1,
        )
        patched = patched.replace(
            '        auth_state=str(auth) if auth.is_file() else None,\n',
            '        auth_state=auth_state,\n',
            1,
        )
    return patched


def patch_process_manager_source(source: str) -> str:
    if PROCESS_MANAGER_MARKER in source:
        return source
    permission_block = (
        '        except PermissionError as e:\n'
        '            return {\n'
        '                "ok": False,\n'
        '                "reason": "permission denied",\n'
        '                "error": str(e),\n'
        '                "pid": pid,\n'
        '                "meetingId": active.get("meeting_id"),\n'
        '                "transcriptPath": str(transcript_path) if transcript_path else None,\n'
        '                "hint": "Run sg-hermes-meet stop from the host to clean up a legacy root-owned bot.",\n'
        '            }\n'
    )
    return _replace_many(source, [
        (
            'def stop(*, reason: str = "requested") -> Dict[str, Any]:\n',
            f'{PROCESS_MANAGER_MARKER}\ndef stop(*, reason: str = "requested") -> Dict[str, Any]:\n',
            'stop function',
        ),
        (
            '        except ProcessLookupError:\n            pass\n        for _ in range(20):\n',
            '        except ProcessLookupError:\n            pass\n' + permission_block + '        for _ in range(20):\n',
            'SIGTERM permission handling',
        ),
        (
            '            except ProcessLookupError:\n                pass\n\n    _clear_active()\n',
            '            except ProcessLookupError:\n                pass\n'
            '            except PermissionError as e:\n'
            '                return {"ok": False, "reason": "permission denied", "error": str(e), "pid": pid, "meetingId": active.get("meeting_id"), "hint": "Run sg-hermes-meet stop from the host."}\n\n'
            '    _clear_active()\n',
            'SIGKILL permission handling',
        ),
    ])


def patch_init_source(source: str) -> str:
    if INIT_MARKER in source:
        return source
    old = (
        'from plugins.google_meet.cli import register_cli as _register_meet_cli\n'
        'from plugins.google_meet.cli import meet_command as _meet_command\n'
    )
    new = (
        f'{INIT_MARKER}\n'
        'def _register_meet_cli(subparser):\n'
        '    from plugins.google_meet.cli import register_cli\n'
        '    return register_cli(subparser)\n\n'
        'def _meet_command(args):\n'
        '    from plugins.google_meet.cli import meet_command\n'
        '    return meet_command(args)\n'
    )
    if old not in source:
        raise ValueError(f'anchor block not found in {INIT_TARGET}: eager cli imports')
    return source.replace(old, new, 1)


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
    return f'Patched: {path} - Google Meet transcript capture hardened'


def main() -> None:
    try:
        print(patch_file(MEET_BOT_TARGET, patch_meet_bot_source, 'meet_bot'))
        print(patch_file(CLI_TARGET, patch_cli_source, 'cli'))
        print(patch_file(PROCESS_MANAGER_TARGET, patch_process_manager_source, 'process_manager'))
        print(patch_file(INIT_TARGET, patch_init_source, 'init'))
    except ValueError as exc:
        print(f'FATAL: {exc} - upstream may have changed', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
