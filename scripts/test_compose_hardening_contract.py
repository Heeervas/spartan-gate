from __future__ import annotations

import json
import os
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infra" / "compose"
TIERS = COMPOSE / "tiers"

MATRICES = {
    "l0": (TIERS / "compose.l0.yml",),
    "l0-clawroute": (
        TIERS / "compose.l0.yml",
        TIERS / "compose.clawroute.yml",
    ),
    "l1": (TIERS / "compose.l1.yml",),
    "l1-clawroute": (
        TIERS / "compose.l1.yml",
        TIERS / "compose.clawroute.yml",
    ),
    "l2": (TIERS / "compose.l2.yml",),
    "l3": (
        TIERS / "compose.l2.yml",
        TIERS / "compose.l3.yml",
    ),
    "l1-spartan-route": (
        TIERS / "compose.l1.yml",
        TIERS / "compose.spartan-route.yml",
    ),
    "l2-spartan-route": (
        TIERS / "compose.l2.yml",
        TIERS / "compose.spartan-route.yml",
    ),
    "l4": (COMPOSE / "compose.yml",),
    "l4-dev": (
        COMPOSE / "compose.yml",
        COMPOSE / "compose.dev.yml",
    ),
    "l4-prod": (
        COMPOSE / "compose.yml",
        COMPOSE / "compose.prod.example.yml",
    ),
    "l4-private-example": (
        COMPOSE / "compose.yml",
        ROOT / "private.example" / "compose.local.yml",
    ),
    "l4-route-compat": (
        COMPOSE / "compose.yml",
        COMPOSE / "compose.spartan-route.yml",
    ),
}

MIB = 1024 * 1024
EXPECTED_RESOURCES = {
    "proxy": (64 * MIB, 128),
    "searxng": (256 * MIB, 128),
    "reader": (128 * MIB, 64),
    "dns": (32 * MIB, 64),
    "spartan-route": (512 * MIB, 256),
    "clawroute": (512 * MIB, 256),
    "browserless": (1024 * MIB, 512),
    "camofox": (2048 * MIB, 512),
    "pocket-tts": (2048 * MIB, 128),
    "pocket-tts-prefetch": (2048 * MIB, 128),
    "hermes": (2048 * MIB, 512),
    "caddy": (64 * MIB, 128),
}

HERMES_CAPABILITIES = {
    "CHOWN",
    "FOWNER",
    "SETGID",
    "SETUID",
    "DAC_OVERRIDE",
    "NET_BIND_SERVICE",
}


def render_compose(paths: tuple[pathlib.Path, ...]) -> dict:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / ".env.example"),
    ]
    for path in paths:
        command.extend(("-f", str(path)))
    command.extend(("config", "--format", "json"))
    env = os.environ.copy()
    env.update(
        {
            "TAILSCALE_IP": "127.0.0.2",
            "SPARTAN_CADDY_UID": "1000",
            "SPARTAN_CADDY_GID": "1000",
            "SPARTAN_ROUTE_UID": "1000",
            "SPARTAN_ROUTE_GID": "1000",
            "SPARTAN_CLAWROUTE_UID": "1000",
            "SPARTAN_CLAWROUTE_GID": "1000",
            "COMPOSE_PROFILES": "camofox",
        }
    )
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Compose render failed for {paths}: {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


class ComposeHardeningContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renders = {
            label: render_compose(paths)
            for label, paths in MATRICES.items()
        }

    def iter_services(self):
        for label, rendered in self.renders.items():
            for name, service in rendered["services"].items():
                yield label, name, service

    def test_every_service_has_its_resource_ceiling(self):
        for label, name, service in self.iter_services():
            with self.subTest(matrix=label, service=name):
                expected_memory, expected_pids = EXPECTED_RESOURCES[name]
                self.assertEqual(int(service["mem_limit"]), expected_memory)
                self.assertEqual(service["pids_limit"], expected_pids)

    def test_hermes_hardening_is_identical_across_layers(self):
        for label, rendered in self.renders.items():
            service = rendered["services"]["hermes"]
            with self.subTest(matrix=label):
                self.assertTrue(service["read_only"])
                self.assertEqual(service["cap_drop"], ["ALL"])
                self.assertEqual(set(service["cap_add"]), HERMES_CAPABILITIES)
                self.assertIn(
                    "no-new-privileges:true",
                    service["security_opt"],
                )
                self.assertIn("/tmp", service["tmpfs"])
                self.assertTrue(
                    any(item.startswith("/run") for item in service["tmpfs"])
                )

    def test_browser_services_keep_intended_runtime_contracts(self):
        for label, name, service in self.iter_services():
            if name not in {"browserless", "camofox"}:
                continue
            with self.subTest(matrix=label, service=name):
                self.assertIn(
                    "no-new-privileges:true",
                    service["security_opt"],
                )
                self.assertIn("/tmp", service["tmpfs"])
                if name == "browserless":
                    self.assertEqual(service["cap_drop"], ["ALL"])

    def test_routers_are_non_root_and_only_data_is_writable(self):
        for label, name, service in self.iter_services():
            if name not in {"spartan-route", "clawroute"}:
                continue
            with self.subTest(matrix=label, service=name):
                self.assertEqual(service["user"], "1000:1000")
                self.assertTrue(service["read_only"])
                self.assertEqual(service["cap_drop"], ["ALL"])
                self.assertIn(
                    "no-new-privileges:true",
                    service["security_opt"],
                )
                self.assertTrue(
                    any(item.startswith("/tmp") for item in service["tmpfs"])
                )
                volumes = {
                    volume["target"]: volume
                    for volume in service["volumes"]
                }
                self.assertFalse(volumes["/app/data"].get("read_only", False))
                self.assertTrue(volumes["/app/config"]["read_only"])
                self.assertTrue(volumes["/codex-home"]["read_only"])
                if name == "spartan-route":
                    self.assertEqual(
                        service["environment"]["PYTHONDONTWRITEBYTECODE"],
                        "1",
                    )
                    self.assertIn(
                        "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
                        service["tmpfs"],
                    )

    def test_caddy_uses_only_image_required_capability_and_is_ready(self):
        for label, rendered in self.renders.items():
            if "caddy" not in rendered["services"]:
                continue
            service = rendered["services"]["caddy"]
            with self.subTest(matrix=label):
                self.assertEqual(service["user"], "1000:1000")
                self.assertTrue(service["read_only"])
                self.assertEqual(service["cap_drop"], ["ALL"])
                self.assertEqual(service["cap_add"], ["NET_BIND_SERVICE"])
                self.assertIn(
                    "no-new-privileges:true",
                    service["security_opt"],
                )
                health = " ".join(service["healthcheck"]["test"])
                self.assertIn("caddy validate", health)
                self.assertIn("127.0.0.1:18789", health)
                self.assertIn("401", health)

    def test_l4_hermes_health_requires_gate_network_after_grace_period(self):
        service = self.renders["l4"]["services"]["hermes"]
        healthcheck = service["healthcheck"]
        health = " ".join(healthcheck["test"])
        self.assertIn("127.0.0.1", health)
        self.assertIn("proxy", health)
        self.assertIn("8888", health)
        self.assertIn("spartan-route", health)
        self.assertIn("18790", health)
        self.assertEqual(healthcheck["interval"], "15s")
        self.assertEqual(healthcheck["retries"], 3)

    def test_camofox_uses_supervised_deep_readiness_in_every_layer(self):
        expected_environment = {
            "MAX_CONCURRENT_PER_USER": "2",
            "MAX_SESSIONS": "8",
            "MAX_TABS_PER_SESSION": "4",
            "HANDLER_TIMEOUT_MS": "30000",
            "NAVIGATE_TIMEOUT_MS": "25000",
            "BUILDREFS_TIMEOUT_MS": "12000",
            "SPARTAN_CAMOFOX_CGROUP_ADMISSION": "true",
            "SPARTAN_CAMOFOX_DEEP_TIMEOUT_MS": "8000",
            "SPARTAN_CAMOFOX_SERVER_RECOVERY_TIMEOUT_MS": "20000",
            "SPARTAN_CAMOFOX_FAILURE_THRESHOLD": "3",
            "SPARTAN_CAMOFOX_BACKOFF_SECONDS": "0,30,120,600,1800",
            "SPARTAN_CAMOFOX_MEMORY_SOFT_PERCENT": "90",
            "SPARTAN_CAMOFOX_MEMORY_HARD_PERCENT": "98",
        }
        for label, rendered in self.renders.items():
            if "camofox" not in rendered["services"]:
                continue
            service = rendered["services"]["camofox"]
            with self.subTest(matrix=label):
                self.assertEqual(service["restart"], "unless-stopped")
                self.assertEqual(service["stop_grace_period"], "1m0s")
                self.assertIn("/run", service["tmpfs"])
                self.assertEqual(
                    service["healthcheck"]["test"],
                    ["CMD", "python3", "/usr/local/bin/spartan-camofox-supervisor.py", "check"],
                )
                self.assertEqual(service["healthcheck"]["timeout"], "3s")
                self.assertEqual(service["healthcheck"]["start_period"], "1m0s")
                for key, value in expected_environment.items():
                    self.assertEqual(service["environment"].get(key), value, key)
                targets = {volume["target"] for volume in service.get("volumes", [])}
                self.assertIn("/var/lib/spartan-camofox-watchdog", targets)
                rendered_contract = json.dumps(service, sort_keys=True)
                self.assertNotIn("docker.sock", rendered_contract)
                self.assertNotIn('"pid": "host"', rendered_contract)
                health = " ".join(service["healthcheck"]["test"]).lower()
                for forbidden in ("curl", "docker", "restart", "kill"):
                    self.assertNotIn(forbidden, health)

    def test_clawroute_image_identity_matches_compose_default(self):
        dockerfile = (
            ROOT / "packages" / "clawroute" / "Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn("USER 1000:1000", dockerfile)


if __name__ == "__main__":
    unittest.main()
