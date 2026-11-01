"""Public document/asset contract; run only through run_tests.py isolation."""
from pathlib import Path
import unittest

from aiohttp.test_utils import TestClient, TestServer
from test_app import test_config
from spartan_route.app import create_app
from spartan_route.telemetry import TelemetryStore


class AccountsPagesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_app(test_config(), TelemetryStore(":memory:"))
        # Static documents need no application/provider startup. Seal it explicitly.
        self.app.on_startup.clear()
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_documents_aliases_and_assets_keep_auth_and_private_cache(self):
        documents = {
            "/": "dashboard-codex.html", "/accounts": "dashboard-codex.html",
            "/activity": "dashboard.html", "/settings": "dashboard-config.html",
            "/dashboard": "dashboard.html", "/dashboard-codex": "dashboard-codex.html",
            "/dashboard-config": "dashboard-config.html",
        }
        assets = ["dashboard-codex.js", "accounts.css", "shell.css"]
        paths = {**documents, **{f"/dashboard-assets/{name}": f"assets/{name}" for name in assets}}
        root = Path(__file__).parent / "web"
        for path, filename in paths.items():
            with self.subTest(path=path):
                denied = await self.client.get(path)
                self.assertEqual(denied.status, 401)
                wrong = await self.client.get(path, headers={"Authorization": "Bearer wrong"})
                self.assertEqual(wrong.status, 401)
                response = await self.client.get(path + "?preserve=value", headers={"Authorization": "Bearer secret"}, allow_redirects=False)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "private, no-cache")
                self.assertEqual(await response.text(), (root / filename).read_text())
        for name in ("not-allowed.js", "overview.js", "overview.css"):
            unknown = await self.client.get(f"/dashboard-assets/{name}", headers={"Authorization": "Bearer secret"})
            self.assertEqual(unknown.status, 404)

    async def test_all_documents_have_the_same_navigation_and_existing_controllers(self):
        pages = {"/": ("/", "dashboard-codex.js"), "/accounts": ("/", "dashboard-codex.js"),
                 "/activity": ("/activity", "dashboard.js"), "/settings": ("/settings", "dashboard-config.js")}
        for path, (current, script) in pages.items():
            response = await self.client.get(path, headers={"Authorization": "Bearer secret"})
            self.assertEqual(response.status, 200)
            html = await response.text()
            for href, label in [("/", "Accounts"), ("/activity", "Activity"), ("/settings", "Settings")]:
                self.assertIn(f'>{label}</a>', html)
                self.assertIn(f'href="{href}"', html)
            self.assertIn(f'href="{current}" aria-current="page"', html)
            self.assertEqual(html.count('aria-current="page"'), 1)
            self.assertNotIn('>Overview</a>', html)
            self.assertIn(f'src="/dashboard-assets/{script}"', html)
