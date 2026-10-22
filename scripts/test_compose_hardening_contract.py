from __future__ import annotations

import json
import os
import pathlib
import subprocess
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infra" / "compose"
TIERS = COMPOSE / "tiers"

MATRICES = {
    "l0": (TIERS / "compose.l0.yml",),
    "l1": (TIERS / "compose.l1.yml",),
    "l2": (TIERS / "compose.l2.yml",),
    "l3": (
        TIERS / "compose.l2.yml",
        TIERS / "compose.l3.yml",
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
    "edge-sessions": (64 * MIB, 32),
    "searxng": (256 * MIB, 128),
    "reader": (128 * MIB, 64),
    "dns": (32 * MIB, 64),
    "spartan-route": (512 * MIB, 256),
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
            "COMPOSE_PROFILES": "camofox,pocket-tts",
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


def render_identity(path: pathlib.Path, overrides: dict[str, str] | None = None) -> dict:
    keys = {
        "SPARTAN_HERMES_RUN_UID",
        "SPARTAN_HERMES_RUN_GID",
        "HERMES_UID",
        "HERMES_GID",
        "PUID",
        "PGID",
    }
    env = {key: value for key, value in os.environ.items() if key not in keys}
    env.update(overrides or {})
    result = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "-f", str(path), "config", "--format", "json"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"Identity render failed for {path}: {result.stderr.strip()}")
    return json.loads(result.stdout)["services"]["hermes"]["environment"]


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
                self.assertIn("CLOUDFLARE_API_TOKEN", service["environment"])
                self.assertIn(
                    "no-new-privileges:true",
                    service["security_opt"],
                )
                self.assertIn("/tmp", service["tmpfs"])
                self.assertTrue(
                    any(item.startswith("/run") for item in service["tmpfs"])
                )
                policy_targets = {
                    "/opt/hermes/spartan_browser_policy.py",
                    "/opt/hermes/bootstrap/spartan_browser_policy.py",
                }
                installed_mounts = [
                    volume for volume in service.get("volumes", [])
                    if isinstance(volume, dict)
                    and str(volume.get("target", "")).startswith("/opt/hermes")
                ]
                self.assertEqual(
                    {volume["target"] for volume in installed_mounts},
                    policy_targets,
                )
                self.assertEqual(len(installed_mounts), len(policy_targets))
                for volume in installed_mounts:
                    self.assertEqual(volume["type"], "bind")
                    self.assertEqual(
                        volume["source"],
                        str(ROOT / "apps/hermes-agent/spartan_browser_policy.py"),
                    )
                    self.assertTrue(volume["read_only"])
                    # Compose omits the false value from normalized JSON.
                    self.assertIs(volume["bind"].get("create_host_path", False), False)

    def test_policy_helper_sources_explicitly_disable_host_path_creation(self):
        for path in (COMPOSE / "compose.yml", *(TIERS / f"compose.l{i}.yml" for i in range(3))):
            with self.subTest(path=path.name):
                service = yaml.safe_load(path.read_text())["services"]["hermes"]
                mounts = {
                    volume["target"]: volume
                    for volume in service["volumes"] if isinstance(volume, dict)
                }
                for target in (
                    "/opt/hermes/spartan_browser_policy.py",
                    "/opt/hermes/bootstrap/spartan_browser_policy.py",
                ):
                    volume = mounts[target]
                    self.assertEqual(volume["type"], "bind")
                    self.assertTrue(volume["read_only"])
                    self.assertIs(volume["bind"]["create_host_path"], False)
                    source = (path.parent / volume["source"]).resolve()
                    self.assertEqual(source, ROOT / "apps/hermes-agent/spartan_browser_policy.py")

    def test_private_https_proxy_trust_is_restricted_to_the_internal_subnet(self):
        env = self.renders["l4-private-example"]["services"]["hermes"]["environment"]
        trusted = env.get("FORWARDED_ALLOW_IPS", "")
        self.assertTrue(trusted)
        self.assertNotEqual(trusted, "*")
        self.assertEqual(trusted, "172.28.0.0/24")

    def test_hermes_identity_defaults_and_legacy_values_render_without_synthetic_conflicts(self):
        for path in (
            TIERS / "compose.l0.yml",
            TIERS / "compose.l1.yml",
            TIERS / "compose.l2.yml",
            COMPOSE / "compose.yml",
        ):
            with self.subTest(path=path, case="default"):
                env = render_identity(path)
                self.assertEqual(env["SPARTAN_HERMES_RUN_UID"], "1000")
                self.assertEqual(env["SPARTAN_HERMES_RUN_GID"], "1000")
                self.assertEqual(env["HERMES_UID"], "")
                self.assertEqual(env["HERMES_GID"], "")
            with self.subTest(path=path, case="legacy-non-1000"):
                env = render_identity(path, {"HERMES_UID": "2000", "HERMES_GID": "2001"})
                self.assertEqual(env["SPARTAN_HERMES_RUN_UID"], "2000")
                self.assertEqual(env["SPARTAN_HERMES_RUN_GID"], "2001")
                self.assertEqual(env["HERMES_UID"], "2000")
                self.assertEqual(env["HERMES_GID"], "2001")
            with self.subTest(path=path, case="puid-pgid-non-1000"):
                env = render_identity(path, {"PUID": "2100", "PGID": "2101"})
                self.assertEqual(env["SPARTAN_HERMES_RUN_UID"], "2100")
                self.assertEqual(env["SPARTAN_HERMES_RUN_GID"], "2101")
                self.assertEqual(env["PUID"], "2100")
                self.assertEqual(env["PGID"], "2101")
            with self.subTest(path=path, case="explicit-conflict-visible"):
                env = render_identity(
                    path,
                    {
                        "SPARTAN_HERMES_RUN_UID": "3000",
                        "SPARTAN_HERMES_RUN_GID": "3001",
                        "HERMES_UID": "2000",
                        "HERMES_GID": "2001",
                    },
                )
                self.assertEqual(env["SPARTAN_HERMES_RUN_UID"], "3000")
                self.assertEqual(env["HERMES_UID"], "2000")

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
            "SPARTAN_CAMOFOX_MAX_CONCURRENT_CREATES": "1",
            "SPARTAN_CAMOFOX_DEEP_CLEANUP_TIMEOUT_MS": "1000",
            "SPARTAN_CAMOFOX_DEEP_BUSY_MAX_AGE_MS": "45000",
            "SPARTAN_CAMOFOX_RECOVERY_DRAIN_TIMEOUT_MS": "2000",
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
            "SPARTAN_CAMOFOX_MEMORY_SOFT_RATIO": "0.75",
            "SPARTAN_CAMOFOX_MEMORY_EVICT_RATIO": "0.82",
            "SPARTAN_CAMOFOX_MEMORY_RESET_RATIO": "0.70",
            "SPARTAN_CAMOFOX_MEMORY_SOFT_PERCENT": "82",
            "SPARTAN_CAMOFOX_MEMORY_HARD_PERCENT": "90",
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

    def test_l3_route_is_internal_and_caddy_upstream_is_present(self):
        services = self.renders['l3']['services']
        route = services['spartan-route']
        self.assertEqual(set(route['networks']), {'spartan_internal'})
        self.assertFalse(route.get('ports'))
        self.assertEqual(route['environment']['HTTPS_PROXY'], 'http://proxy:8888')
        self.assertEqual(services['hermes']['environment']['OPENAI_BASE_URL'],
                         'http://spartan-route:18790/v1')
        targets = {item['target'] for item in services['caddy']['volumes']}
        self.assertIn('/etc/caddy/route.d', targets)
        l2_targets = {item['target'] for item in self.renders['l2']['services']['caddy']['volumes']}
        self.assertNotIn('/etc/caddy/route.d', l2_targets)

    def test_gate_layers_share_private_whitelist_at_every_consumer(self):
        for layer in ('l2', 'l3'):
            consumers = self.renders[layer]['services']
            sources = []
            for service, target in (('proxy', '/etc/tinyproxy/whitelist.private.txt'),
                                    ('dns', '/etc/dns-whitelist.private.txt'),
                                    ('hermes', '/etc/proxy-whitelist.private.txt')):
                mounts = [item for item in consumers[service]['volumes'] if item['target'] == target]
                self.assertEqual(len(mounts), 1, (layer, service))
                self.assertTrue(mounts[0]['read_only'])
                sources.append(mounts[0]['source'])
            self.assertEqual(len(set(sources)), 1, layer)

    def test_verifier_is_only_on_caddy_private_network(self):
        for layer in ('l2', 'l3', 'l4'):
            rendered = self.renders[layer]
            verifier = rendered['services']['edge-sessions']
            self.assertEqual(set(verifier['networks']), {'spartan_edge_auth'})
            self.assertTrue(rendered['networks']['spartan_edge_auth']['internal'])
            self.assertFalse(verifier.get('ports'))
            peers = {name for name, service in rendered['services'].items()
                     if 'spartan_edge_auth' in service.get('networks', {})}
            self.assertEqual(peers, {'edge-sessions', 'caddy'})
            self.assertTrue(verifier['read_only'])
            self.assertEqual(verifier['cap_drop'], ['ALL'])

    def test_retired_services_are_absent_in_supported_shapes(self):
        for name, config in self.renders.items():
            with self.subTest(shape=name):
                self.assertNotIn('browserless', config['services'])
                self.assertNotIn('clawroute', config['services'])


if __name__ == "__main__":
    unittest.main()
