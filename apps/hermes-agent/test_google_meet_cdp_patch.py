import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).parent / 'patches' / 'patch_google_meet_cdp.py'


def load_module():
    spec = importlib.util.spec_from_file_location('patch_google_meet_cdp', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def meet_bot_source(module):
    return (
        module.MEET_BOT_HELPER_ANCHOR
        + '\n'
        + module.MEET_BOT_OLD_LAUNCH_BLOCK
        + '\n'
        + module.MEET_BOT_OLD_TEARDOWN_BLOCK
        + '\n'
        + module.MEET_BOT_STATE_FIELDS_ANCHOR
        + '\n'
        + module.MEET_BOT_STATUS_FIELDS_ANCHOR
        + '\n'
        + module.MEET_BOT_JOIN_CALL_BLOCK
        + '\n'
        + module.MEET_BOT_DENIED_BLOCK
        + '\n'
        + module.MEET_BOT_FINAL_STATUS_BLOCK
        + '\n'
        + module.MEET_BOT_OLD_TRY_GUEST_NAME_BLOCK
        + '\n'
        + module.MEET_BOT_OLD_DETECT_ADMISSION_BLOCK
        + '\n'
        + module.MEET_BOT_OLD_DETECT_DENIED_BLOCK
        + '\n'
        + module.MEET_BOT_OLD_CLICK_JOIN_BLOCK
    )


class GoogleMeetCdpPatchTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_meet_bot_patch_adds_cdp_launch_teardown_and_debug_status(self):
        patched = self.module.patch_meet_bot_source(meet_bot_source(self.module))

        self.assertIn(self.module.MEET_BOT_MARKER, patched)
        self.assertIn(self.module.MEET_BOT_LOCALE_MARKER, patched)
        self.assertIn(self.module.MEET_BOT_READY_MARKER, patched)
        self.assertIn('pw.chromium.connect_over_cdp(cdp_url)', patched)
        self.assertIn('HERMES_MEET_CDP_PROFILE", "guest"', patched)
        self.assertIn('BROWSER_CDP_MAIN_URL', patched)
        self.assertIn('BROWSER_CDP_URL', patched)
        self.assertIn('BROWSERLESS_PROFILE', patched)
        self.assertIn('def _meet_camofox_mode_enabled() -> bool:', patched)
        self.assertIn('class _CamofoxMeetClient:', patched)
        self.assertIn('return "", "camofox"', patched)
        self.assertIn('context.grant_permissions(["microphone", "camera"]', patched)
        self.assertIn('if cdp_profile != "main":', patched)
        self.assertIn('pw.chromium.launch(', patched)
        self.assertIn('guestNameAttempted', patched)
        self.assertIn('joinButtonClicked', patched)
        self.assertIn('debugCaptures', patched)

    def test_meet_bot_patch_adds_localized_join_detection_and_debug_capture(self):
        patched = self.module.patch_meet_bot_source(meet_bot_source(self.module))

        self.assertIn('input[aria-label*="nombre" i]', patched)
        self.assertIn('input[placeholder*="nombre" i]', patched)
        self.assertIn('textarea[aria-label*="nombre" i]', patched)
        self.assertIn('Join now', patched)
        self.assertIn('Ask to join', patched)
        self.assertIn('Unirse ahora', patched)
        self.assertIn('Solicitar unirse', patched)
        self.assertIn('Pedir unirse', patched)
        self.assertIn('Solicitar acceso', patched)
        self.assertIn('Unirte ahora', patched)
        self.assertIn('salir de la llamada', patched)
        self.assertIn('participantes', patched)
        self.assertIn('ubtítulos', patched)
        self.assertIn('no puedes unirte a esta videollamada', patched)
        self.assertIn('te han eliminado de la reunion', patched)
        self.assertIn('nadie respondio a tu solicitud para unirte', patched)
        self.assertIn('no se te ha admitido', patched)
        self.assertIn('def _capture_debug', patched)
        self.assertIn('debug_{safe_reason}.png', patched)
        self.assertIn('debug_{safe_reason}_buttons.json', patched)
        self.assertIn('valueLength', patched)

    def test_meet_bot_patch_marks_lobby_buttons(self):
        patched = self.module.patch_meet_bot_source(meet_bot_source(self.module))

        self.assertIn('_LOBBY_JOIN_LABELS = {"Ask to join", "Solicitar unirse", "Pedir unirse", "Solicitar acceso"}', patched)
        self.assertIn('lobby_waiting=label in _LOBBY_JOIN_LABELS', patched)

    def test_meet_bot_patch_waits_for_join_controls_before_joining(self):
        patched = self.module.patch_meet_bot_source(meet_bot_source(self.module))

        self.assertIn('def _wait_for_join_controls(', patched)
        self.assertIn('timeout_ms: int = 60_000', patched)
        self.assertIn('join_controls_not_ready_timeout', patched)
        self.assertIn('visible(el) && enabled(el)', patched)
        self.assertIn('input[aria-label*=\"name\" i]', patched)
        self.assertIn('require_join_button=True', patched)
        self.assertIn('timeout_ms=60_000, require_join_button=True', patched)
        self.assertLess(
            patched.index('_wait_for_join_controls(page, state)'),
            patched.index('_try_guest_name(page, guest_name, state)'),
        )

    def test_meet_bot_patch_routes_camofox_without_playwright_import(self):
        source = (
            self.module.MEET_BOT_HELPER_ANCHOR
            + '\n'
            + '    if rt["enabled"]:\n        if not realtime_api_key:\n'
            + '\n'
            + self.module.MEET_BOT_OLD_IMPORT_BLOCK
            + '\n'
            + self.module.MEET_BOT_OLD_LAUNCH_BLOCK
            + '\n'
            + self.module.MEET_BOT_OLD_TEARDOWN_BLOCK
            + '\n'
            + self.module.MEET_BOT_STATE_FIELDS_ANCHOR
            + '\n'
            + self.module.MEET_BOT_STATUS_FIELDS_ANCHOR
            + '\n'
            + self.module.MEET_BOT_JOIN_CALL_BLOCK
            + '\n'
            + self.module.MEET_BOT_DENIED_BLOCK
            + '\n'
            + self.module.MEET_BOT_FINAL_STATUS_BLOCK
            + '\n'
            + self.module.MEET_BOT_OLD_TRY_GUEST_NAME_BLOCK
            + '\n'
            + self.module.MEET_BOT_OLD_DETECT_ADMISSION_BLOCK
            + '\n'
            + self.module.MEET_BOT_OLD_DETECT_DENIED_BLOCK
            + '\n'
            + self.module.MEET_BOT_OLD_CLICK_JOIN_BLOCK
        )

        patched = self.module.patch_meet_bot_source(source)

        self.assertIn('sync_playwright = _camofox_sync_playwright', patched)
        self.assertIn('realtime mode requested with Camofox backend - falling back to transcribe', patched)
        self.assertIn('class _CamofoxMeetPage:', patched)

    def test_meet_bot_patch_is_idempotent_for_already_fully_patched_source(self):
        source = f'before\n{self.module.MEET_BOT_MARKER}\n{self.module.MEET_BOT_LOCALE_MARKER}\n{self.module.MEET_BOT_READY_MARKER}\nafter\n'

        self.assertEqual(self.module.patch_meet_bot_source(source), source)

    def test_meet_bot_patch_migrates_existing_locale_patch_to_ready_wait(self):
        source = (
            f'before\n{self.module.MEET_BOT_MARKER}\n'
            + self.module.MEET_BOT_NEW_TRY_GUEST_NAME_BLOCK
            + '\n'
            + self.module.MEET_BOT_READY_JOIN_CALL_BLOCK
            + 'after\n'
        )

        patched = self.module.patch_meet_bot_source(source)

        self.assertIn(self.module.MEET_BOT_READY_MARKER, patched)
        self.assertIn('def _wait_for_join_controls(', patched)
        self.assertIn('join_controls_not_ready_timeout', patched)
        self.assertIn('if _wait_for_join_controls(page, state):', patched)
        self.assertNotIn(self.module.MEET_BOT_READY_JOIN_CALL_BLOCK, patched)

    def test_meet_bot_patch_migrates_existing_cdp_patch_to_locale_patch(self):
        legacy_profile = (
            '    profile = os.environ.get("HERMES_MEET_CDP_PROFILE", "guest").strip().lower()\n'
            '    if profile == "main":\n'
            '        return os.environ.get("BROWSER_CDP_MAIN_URL", "").strip(), "main"\n'
        )
        source = (
            f'before\n{self.module.MEET_BOT_MARKER}\n'
            + legacy_profile
            + self.module.MEET_BOT_STATE_FIELDS_ANCHOR
            + self.module.MEET_BOT_STATUS_FIELDS_ANCHOR
            + self.module.MEET_BOT_JOIN_CALL_BLOCK
            + self.module.MEET_BOT_DENIED_BLOCK
            + self.module.MEET_BOT_FINAL_STATUS_BLOCK
            + self.module.MEET_BOT_OLD_TRY_GUEST_NAME_BLOCK
            + self.module.MEET_BOT_OLD_DETECT_ADMISSION_BLOCK
            + self.module.MEET_BOT_OLD_DETECT_DENIED_BLOCK
            + self.module.MEET_BOT_OLD_CLICK_JOIN_BLOCK
            + '\nafter\n'
        )

        patched = self.module.patch_meet_bot_source(source)

        self.assertIn(self.module.MEET_BOT_LOCALE_MARKER, patched)
        self.assertIn('browserless_profile = os.environ.get("BROWSERLESS_PROFILE", "main")', patched)
        self.assertIn('Solicitar unirse', patched)

    def test_meet_bot_patch_migrates_existing_cdp_patch_to_camofox_backend(self):
        source = (
            f'before\n{self.module.MEET_BOT_MARKER}\n'
            'SAY_PCM_FILENAME = "speaker.pcm"\n\n\n'
            'def _resolve_meet_cdp() -> tuple[str, str]:\n'
            '    return os.environ.get("BROWSER_CDP_URL", "").strip(), "guest"\n'
            '\n'
            'def _is_safe_meet_url(url: str) -> bool:\n'
            '    return True\n'
            '    cdp_url, cdp_profile = _resolve_meet_cdp()\n'
            'after\n'
        )

        patched = self.module.patch_meet_bot_source(source)

        self.assertIn('def _meet_camofox_mode_enabled() -> bool:', patched)
        self.assertIn('class _CamofoxMeetClient:', patched)
        self.assertIn('return "", "camofox"', patched)
        self.assertNotIn('_raise_meet_cdp_required_in_camofox_mode()', patched)

    def test_meet_bot_patch_migrates_initial_teardown(self):
        source = (
            f'before\n{self.module.MEET_BOT_MARKER}\n'
            'if cdp_profile != "main" and not cdp_shared_context:\n'
            'after\n'
        )

        patched = self.module.patch_meet_bot_source(source)

        self.assertIn('if cdp_profile != "main":', patched)
        self.assertNotIn('and not cdp_shared_context', patched)

    def test_meet_bot_patch_raises_when_anchor_changes(self):
        with self.assertRaises(ValueError):
            self.module.patch_meet_bot_source('before\nafter\n')

    def test_meet_bot_patch_is_atomic_when_a_late_anchor_is_missing(self):
        source = meet_bot_source(self.module).replace(
            self.module.MEET_BOT_FINAL_STATUS_BLOCK,
            'changed upstream final status block',
        )

        with self.assertRaises(ValueError):
            self.module.patch_meet_bot_source(source)

    def test_cli_patch_makes_setup_cdp_aware(self):
        source = (
            self.module.CLI_OLD_HELPER_BLOCK
            + '\n'
            + self.module.CLI_OLD_SETUP_BLOCK
        )

        patched = self.module.patch_cli_source(source)

        self.assertIn(self.module.CLI_MARKER, patched)
        self.assertIn('browser backend: camofox  [transcribe]', patched)
        self.assertIn('return "", "camofox"', patched)
        self.assertIn('CAMOFOX_USER_ID', patched)
        self.assertIn('browser cdp', patched)
        self.assertIn('connect_over_cdp(cdp_url)', patched)
        self.assertIn('not required — using external CDP', patched)
        self.assertIn('Browserless profile {browserless_profile}', patched)
        self.assertIn('custom CDP profile', patched)
        self.assertIn('cdp_profile in {"main", "custom"}', patched)
        self.assertIn('google_signed_in = None', patched)
        self.assertIn('__Secure-3PSID', patched)
        self.assertIn('Google sign-in not detected', patched)
        self.assertIn('sg stop hermes && ', patched)
        self.assertIn('sg-browserless-profile-chrome {browserless_profile} https://accounts.google.com; ', patched)
        self.assertIn('close Chrome after login; sg up -d hermes', patched)
        self.assertIn('all_ok = system_ok and pw_ok and (cdp_ok or chromium_ok) and google_signed_in is not False', patched)

    def test_cli_patch_is_idempotent(self):
        source = f'before\n{self.module.CLI_MARKER}\nafter\n'

        self.assertEqual(self.module.patch_cli_source(source), source)

    def test_cli_patch_migrates_google_sign_in_preflight_message(self):
        source = (
            f'before\n{self.module.CLI_MARKER}\n'
            '    google_signed_in = None\n'
            '        elif google_signed_in is False:\n'
            '            auth_msg += (\n'
            '                " (Google sign-in not detected - run: "\n'
            '                f"sg-browserless-profile-live {browserless_profile} https://accounts.google.com"\n'
            '                ")"\n'
            '            )\n'
            'after\n'
        )

        patched = self.module.patch_cli_source(source)

        self.assertIn('sg stop hermes && ', patched)
        self.assertIn('sg-browserless-profile-chrome {browserless_profile} https://accounts.google.com; ', patched)
        self.assertIn('close Chrome after login; sg up -d hermes', patched)
        self.assertNotIn('Google sign-in not detected - run: ', patched)

    def test_cli_patch_migrates_existing_cdp_setup_to_camofox_backend(self):
        source = (
            f'before\n{self.module.CLI_MARKER}\n'
            'def _resolve_meet_cdp() -> tuple[str, str]:\n'
            '    return os.environ.get("BROWSER_CDP_URL", "").strip(), "guest"\n'
            'def _cmd_setup() -> int:\n'
            '    print("google_meet preflight")\n'
            '    print("---------------------")\n\n'
            '    cdp_url, cdp_profile = _resolve_meet_cdp()\n'
            'after\n'
        )

        patched = self.module.patch_cli_source(source)

        self.assertIn('def _meet_camofox_mode_enabled() -> bool:', patched)
        self.assertIn('browser backend: camofox  [transcribe]', patched)
        self.assertIn('return "", "camofox"', patched)
        self.assertLess(
            patched.index('if _meet_camofox_mode_enabled():'),
            patched.index('cdp_url, cdp_profile = _resolve_meet_cdp()'),
        )

    def test_cli_patch_raises_when_anchor_changes(self):
        with self.assertRaises(ValueError):
            self.module.patch_cli_source('before\nafter\n')

    def test_cli_patch_is_atomic_when_setup_anchor_is_missing(self):
        source = self.module.CLI_OLD_HELPER_BLOCK + '\nchanged upstream setup block\n'

        with self.assertRaises(ValueError):
            self.module.patch_cli_source(source)


if __name__ == '__main__':
    unittest.main()
