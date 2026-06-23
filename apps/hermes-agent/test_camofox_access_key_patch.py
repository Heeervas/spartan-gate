import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name('patches').joinpath(
    'patch_camofox_access_key.py'
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        'patch_camofox_access_key',
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


SOURCE = '''from typing import Any, Dict, Optional
import os
import requests


def get_camofox_url() -> str:
    """Return the configured Camofox server URL, or empty string."""
    return os.getenv("CAMOFOX_URL", "").rstrip("/")


def is_camofox_mode() -> bool:
    """True when Camofox backend is configured and no CDP override is active.
    """
    if os.getenv("BROWSER_CDP_URL", "").strip():
        return False
    return bool(get_camofox_url())


def check_camofox_available() -> bool:
    resp = requests.get(f"{url}/health", timeout=5)
    return resp.status_code == 200


def _ensure_tab(session, base, url):
    resp = requests.post(
        f"{base}/tabs",
        json={
            "userId": session["user_id"],
            "sessionKey": session["session_key"],
            "url": url,
        },
        timeout=_DEFAULT_TIMEOUT,
    )
    return resp.json()


def _post(path: str, body: dict, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    resp = requests.post(url, json=body, timeout=timeout)
    return resp.json()


def _get(path: str, params: dict = None, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    resp = requests.get(url, params=params, timeout=timeout)
    return resp.json()


def _get_raw(path: str, params: dict = None, timeout: int = _DEFAULT_TIMEOUT) -> requests.Response:
    resp = requests.get(url, params=params, timeout=timeout)
    return resp


def _delete(path: str, body: dict = None, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    resp = requests.delete(url, json=body, timeout=timeout)
    return resp.json()
'''


class CamofoxAccessKeyPatchTests(unittest.TestCase):
    def test_adds_bearer_headers_to_camofox_requests(self):
        module = load_module()

        patched = module.patch_source(SOURCE)

        self.assertIn(module.MARKER, patched)
        self.assertIn('def _camofox_request_headers() -> Dict[str, str]:', patched)
        self.assertIn('os.getenv("CAMOFOX_ACCESS_KEY", "").strip()', patched)
        self.assertIn('headers=_camofox_request_headers()', patched)
        self.assertEqual(patched.count('headers=_camofox_request_headers()'), 6)

    def test_idempotent_when_marker_present(self):
        module = load_module()
        source = f'{module.MARKER}\n{SOURCE}'

        self.assertEqual(module.patch_source(source), source)


if __name__ == '__main__':
    unittest.main()
