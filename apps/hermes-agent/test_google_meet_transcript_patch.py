import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).parent / 'patches' / 'patch_google_meet_transcript.py'


def load_module():
    spec = importlib.util.spec_from_file_location('patch_google_meet_transcript', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def meet_bot_source():
    return '''class _BotState:
    def __init__(self):
        self.join_button_error: Optional[str] = None
        self.last_debug_capture: Optional[str] = None

    def _flush(self):
        data = {
            "joinButtonError": self.join_button_error,
            "lastDebugCapture": self.last_debug_capture,
        }

# JavaScript injected into the Meet tab to observe captions. Captures old tuples.
_CAPTION_OBSERVER_JS = r"""
(() => { window.__hermesMeetInstalled = true; })();
"""


def _enable_captions_js() -> str:
    """old captions"""
    return r"""
    (() => true)();
    """


def _start_realtime_speaker(
    *,
    rt: dict,
) -> None:
    pass


def _detect_admission(page) -> bool:
    """old admission"""
    try:
        return bool(page.evaluate("false"))
    except Exception:
        return False


def _detect_denied(page):
    return None

_JOIN_BUTTONS = (
    "Join now",
    "Ask to join",
    "Unirse ahora",
    "Solicitar unirse",
    "Pedir unirse",
    "Solicitar acceso",
    "Unirte ahora",
)

async_marker = "keep"

                if not _detect_admission(page) and _wait_for_join_controls(page, state, timeout_ms=60_000, require_join_button=True):
                    _click_join(page, state)

            # Install caption observer and attempt to enable captions.
            try:
                page.evaluate(_enable_captions_js())
                state.set(captions_enabled_attempted=True)
            except Exception:
                pass
            try:
                page.evaluate(_CAPTION_OBSERVER_JS)
            except Exception as e:
                state.set(error=f"caption observer install failed: {e}")

            # Note: in_call=False until admission is confirmed (we detect
            last_admission_check = 0.0
            while not stop_flag["stop"]:
                pass
                    if admitted:
                        state.set(
                            in_call=True,
                            lobby_waiting=False,
                            joined_at=now,
                        )
                try:
                    queued = page.evaluate("window.__hermesMeetDrain && window.__hermesMeetDrain()")
'''


class GoogleMeetTranscriptPatchTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_meet_bot_patch_adds_mute_caption_and_admission_fixes(self):
        patched = self.module.patch_meet_bot_source(meet_bot_source())

        self.assertIn(self.module.MEET_BOT_MARKER, patched)
        self.assertIn('self.mic_muted_before_join = False', patched)
        self.assertIn('"captionRegionFound": self.caption_region_found', patched)
        self.assertIn('def _mute_prejoin_media', patched)
        self.assertIn('permissions', patched) if 'permissions' in meet_bot_source() else None
        self.assertIn('Control+D', patched)
        self.assertIn('window.__hermesMeetDrain = () =>', patched)
        self.assertIn('window.__hermesMeetScanCaptions', patched)
        self.assertIn('[aria-live="polite"]', patched)
        self.assertIn('Unirme ahora', patched)
        self.assertIn('Unirse ahora sin cámara', patched)
        self.assertIn('admission_detected=True', patched)
        self.assertIn('_ensure_caption_capture(page, state)', patched)
        self.assertIn('last_caption_capture_check = 0.0', patched)

    def test_meet_bot_patch_is_idempotent(self):
        source = f'before\n{self.module.MEET_BOT_MARKER}\nafter\n'
        self.assertEqual(self.module.patch_meet_bot_source(source), source)

    def test_meet_bot_full_patch_includes_migrations(self):
        patched = self.module.patch_meet_bot_source(meet_bot_source())

        self.assertEqual(self.module.patch_meet_bot_source(patched), patched)
        self.assertIn(
            'const turnOn = /(turn on captions|show captions|activar subtitulos|mostrar subtitulos)/i;',
            patched,
        )
        self.assertIn('!window.__hermesMeetCaptionClickAttempted', patched)

    def test_meet_bot_patch_is_atomic_when_a_late_anchor_is_missing(self):
        source = meet_bot_source().replace(
            '                try:\n'
            '                    queued = page.evaluate("window.__hermesMeetDrain && window.__hermesMeetDrain()")\n',
            '                changed_upstream_drain()\n',
        )

        with self.assertRaises(ValueError):
            self.module.patch_meet_bot_source(source)

    def test_cli_patch_adds_leave_alias(self):
        source = '''def register_cli(subs):
    subs.add_parser("stop", help="Leave the current meeting")

def meet_command(args):
    sub = getattr(args, "meet_command", None)
    if not sub:
        print("usage: hermes meet {setup,auth,join,status,transcript,say,stop,node}")
    if sub == "stop":
        return _cmd_stop()
'''
        patched = self.module.patch_cli_source(source)

        self.assertIn('subs.add_parser("leave", help="Alias for stop")', patched)
        self.assertIn('if sub in ("stop", "leave"):', patched)
        self.assertIn('stop,leave,node', patched)

    def test_cli_patch_requires_all_leave_alias_anchors(self):
        source = '''def register_cli(subs):
    subs.add_parser("stop", help="Leave the current meeting")

def meet_command(args):
    sub = getattr(args, "meet_command", None)
    if sub == "stop":
        return _cmd_stop()
'''

        with self.assertRaises(ValueError):
            self.module.patch_cli_source(source)

    def test_cli_patch_guards_setup_auth_permission_error(self):
        source = self.module.CLI_MARKER + '''
def _cmd_setup() -> int:
    auth_path = _auth_state_path()
    auth_ok = auth_path.is_file()
    if auth_ok:
        auth_msg = f"ok ({auth_path})"
    else:
        auth_msg = "guest/no saved auth"
'''
        patched = self.module.patch_cli_source(source)

        self.assertIn(self.module.CLI_SETUP_PERMISSION_MARKER, patched)
        self.assertIn('except PermissionError as e:', patched)
        self.assertIn('auth_error', patched)

    def test_cli_patch_guards_join_auth_permission_error(self):
        source = self.module.CLI_MARKER + '''
def _cmd_join(url: str) -> int:
    auth = _auth_state_path()
    res = pm.start(
        url=url,
        auth_state=str(auth) if auth.is_file() else None,
    )
    return 0
'''
        patched = self.module.patch_cli_source(source)

        self.assertIn(self.module.CLI_JOIN_AUTH_MARKER, patched)
        self.assertIn('except PermissionError:', patched)
        self.assertIn('auth_state=auth_state,', patched)
        self.assertNotIn('auth_state=str(auth) if auth.is_file() else None', patched)

    def test_process_manager_patch_reports_permission_error(self):
        source = '''def stop(*, reason: str = "requested") -> Dict[str, Any]:
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for _ in range(20):
            pass
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    _clear_active()
'''
        patched = self.module.patch_process_manager_source(source)

        self.assertIn(self.module.PROCESS_MANAGER_MARKER, patched)
        self.assertIn('except PermissionError as e:', patched)
        self.assertIn('legacy root-owned bot', patched)

    def test_process_manager_patch_requires_all_signal_anchors(self):
        source = '''def stop(*, reason: str = "requested") -> Dict[str, Any]:
    if pid and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for _ in range(20):
            pass

    _clear_active()
'''

        with self.assertRaises(ValueError):
            self.module.patch_process_manager_source(source)

    def test_init_patch_lazy_loads_cli(self):
        source = '''from plugins.google_meet import process_manager as pm
from plugins.google_meet.cli import register_cli as _register_meet_cli
from plugins.google_meet.cli import meet_command as _meet_command
from plugins.google_meet.tools import MEET_JOIN_SCHEMA
'''
        patched = self.module.patch_init_source(source)

        self.assertIn(self.module.INIT_MARKER, patched)
        self.assertIn('def _register_meet_cli(subparser):', patched)
        self.assertIn('from plugins.google_meet.cli import register_cli', patched)
        self.assertNotIn('from plugins.google_meet.cli import register_cli as _register_meet_cli', patched)


if __name__ == '__main__':
    unittest.main()
