from __future__ import annotations

import sys
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.app import create_app
from spartan_route.config import ModelEntry, SpartanRouteConfig
from spartan_route.telemetry import TelemetryStore


def test_config() -> SpartanRouteConfig:
    tier_models = {
        "heartbeat": {"primary": "codex/gpt-5.4-mini", "fallback": "codex/gpt-5.4-mini"},
        "simple": {"primary": "codex/gpt-5.4-mini", "fallback": "codex/gpt-5.4-mini"},
        "moderate": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
        "complex": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
        "frontier-sonnet": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
        "frontier-opus": {"primary": "codex/gpt-5.5", "fallback": "codex/gpt-5.4"},
    }
    models = {
        "anthropic/test": ModelEntry("anthropic/test", "anthropic"),
        "codex/gpt-5.5": ModelEntry("codex/gpt-5.5", "codex"),
        "codex/gpt-5.4": ModelEntry("codex/gpt-5.4", "codex"),
        "codex/gpt-5.4-mini": ModelEntry("codex/gpt-5.4-mini", "codex"),
    }
    return SpartanRouteConfig(
        host="127.0.0.1",
        port=18790,
        token="secret",
        enabled=True,
        dry_run=False,
        debug=False,
        log_content=False,
        db_path=":memory:",
        config_dir=Path("."),
        web_dir=Path("infra/spartan-route/web"),
        provider_profile="codex",
        baseline_model="codex/gpt-5.5",
        tier_models=tier_models,
        models=models,
    )


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = TelemetryStore(":memory:")
        self.app = create_app(test_config(), self.store)
        self.server = TestServer(self.app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_health_and_models_do_not_require_auth(self) -> None:
        health = await self.client.get("/health")
        self.assertEqual(health.status, 200)
        models = await self.client.get("/v1/models")
        self.assertEqual(models.status, 200)
        payload = await models.json()
        self.assertIn("custom-1/clawroute/auto", [item["id"] for item in payload["data"]])

    async def test_api_requires_bearer_token(self) -> None:
        missing = await self.client.get("/api/routing/live")
        self.assertEqual(missing.status, 401)
        ok = await self.client.get("/api/routing/live", headers={"Authorization": "Bearer secret"})
        self.assertEqual(ok.status, 200)

    async def test_unsupported_provider_returns_json_error(self) -> None:
        response = await self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer secret"},
            json={"model": "anthropic/test", "messages": [{"role": "user", "content": "hello"}]},
        )
        self.assertEqual(response.status, 503)
        payload = await response.json()
        self.assertEqual(payload["error"]["code"], "provider_not_supported")


if __name__ == "__main__":
    unittest.main()
