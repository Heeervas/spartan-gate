import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HermesProfileNewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "scripts").mkdir()
        (self.root / "private" / "env").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts" / "hermes-profile-new.sh", self.root / "scripts")
        self.env_file = self.root / "private" / "env" / "local.env"
        self.compose_file = self.root / "private" / "compose.local.yml"
        self.data_dir = self.root / "data"
        self.env_file.write_text(
            f"SPARTAN_HERMES_DATA_PATH={self.data_dir}\n"
            "HERMES_AUTOSTART_PROFILES=\n"
        )
        self.compose_file.write_text(
            "services:\n"
            "  hermes:\n"
            "    environment:\n"
            "      TELEGRAM_ALLOWED_USERS_WORK: ${HERMES_TELEGRAM_ALLOWED_USERS_WORK:-}\n"
            "      DISCORD_HOME_CHANNEL_WORK: ${DISCORD_HOME_CHANNEL_WORK:-}\n"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_script(self, *args):
        return subprocess.run(
            [self.root / "scripts" / "hermes-profile-new.sh", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_manual_autostart_fails_before_mutating_files(self):
        env_before = self.env_file.read_bytes()
        compose_before = self.compose_file.read_bytes()

        result = self.run_script(
            "manual-profile",
            "--manual",
            "--autostart",
            "--telegram",
            "--discord",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manual profiles cannot be autostarted", result.stderr)
        self.assertEqual(self.env_file.read_bytes(), env_before)
        self.assertEqual(self.compose_file.read_bytes(), compose_before)
        self.assertFalse((self.data_dir / "profiles" / "manual-profile").exists())

    def test_existing_profile_fails_before_mutating_files(self):
        profile_env = self.data_dir / "profiles" / "existing" / ".env"
        profile_env.parent.mkdir(parents=True)
        profile_env.write_text("API_SERVER_PORT=8643\n")
        env_before = self.env_file.read_bytes()
        compose_before = self.compose_file.read_bytes()

        result = self.run_script(
            "existing",
            "--gateway",
            "--port",
            "8643",
            "--telegram",
            "--discord",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(self.env_file.read_bytes(), env_before)
        self.assertEqual(self.compose_file.read_bytes(), compose_before)
        self.assertEqual(profile_env.read_text(), "API_SERVER_PORT=8643\n")

    def test_gateway_port_must_be_in_tcp_range(self):
        for port in ("0", "65536"):
            with self.subTest(port=port):
                result = self.run_script(
                    f"profile-{port}",
                    "--gateway",
                    "--port",
                    port,
                    "--no-telegram",
                    "--no-discord",
                    "--no-autostart",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid port", result.stderr)


class CamofoxContractTests(unittest.TestCase):
    def test_doctor_only_requires_camofox_path_when_enabled(self):
        source = (ROOT / "scripts" / "doctor.sh").read_text()
        self.assertIn('camofox_enabled=$(env_value "$env_file" CAMOFOX_URL)', source)
        self.assertIn('SPARTAN_CAMOFOX_*) test -n "$camofox_enabled" || continue', source)
        self.assertIn('key ~ /^SPARTAN_CAMOFOX_/', source)

    def test_novnc_https_has_no_raw_ip_fallback_and_checks_reachability(self):
        source = (ROOT / "scripts" / "aliases.sh").read_text()
        start = source.index("sg_camofox_novnc_url()")
        end = source.index("sg_camofox_request_node()", start)
        helper = source[start:end]
        self.assertIn("HTTPS noVNC requires TAILSCALE_HOST", helper)
        self.assertIn("does not resolve to TAILSCALE_IP", helper)
        self.assertIn("sg_camofox_novnc_reachable", helper)
        https_branch = helper.split('if [[ "$scheme" == "https" ]]', 1)[1].split("else", 1)[0]
        self.assertNotIn('host="$ip"', https_branch)

    def test_default_caddyfile_has_novnc_listener(self):
        source = (ROOT / "infra" / "caddy" / "Caddyfile").read_text()
        self.assertIn(":26080 {", source)
        self.assertIn("reverse_proxy http://camofox:6080", source)


class AliasHelpTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "aliases.sh", self.root / "scripts")

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_sourced(self, command):
        return subprocess.run(
            ["bash", "-lc", f"source scripts/aliases.sh; {command}"],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_grouped_help_commands_are_discoverable(self):
        for command, expected in (
            ("sg -h", "Spartan Gate helpers"),
            ("sg-help", "Spartan Gate helpers"),
            ("sg-tier -h", "Tier helpers"),
            ("sg-hermes -h", "Hermes helpers"),
            ("sg-browser -h", "Browser helpers"),
            ("sg-meet -h", "Meet helpers"),
            ("sg-disk -h", "Disk helpers"),
        ):
            with self.subTest(command=command):
                result = self.run_sourced(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_existing_disk_wrappers_remain_defined(self):
        result = self.run_sourced(
            "declare -F sg-cache-top sg-cache-live sg-cache-clean sg-docker-df sg-disk sg-gog-login"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("sg-cache-top", "sg-cache-live", "sg-cache-clean", "sg-docker-df", "sg-disk", "sg-gog-login"):
            self.assertIn(name, result.stdout)

    def test_private_alias_hook_is_optional_and_ignored_by_default(self):
        result = self.run_sourced("declare -F sg-disk-private >/dev/null")
        self.assertNotEqual(result.returncode, 0)

        (self.root / "private").mkdir()
        (self.root / "private" / "aliases.local.sh").write_text(
            "sg-disk-private() { printf 'private disk helper\\n'; }\n",
            encoding="utf-8",
        )
        result = self.run_sourced("sg-disk-private")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "private disk helper\n")

    def test_tier_set_persists_selection_and_sg_up_uses_tier_compose(self):
        bin_dir, log = self.write_docker_stub("1000:1000")

        result = self.run_sourced_with_path("sg-tier set L1 --with clawroute --hermes free; sg-tier show; sg-up", bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        env_file = self.root / "private" / "env" / "local.env"
        env_content = env_file.read_text(encoding="utf-8")
        self.assertIn("SPARTAN_TIER=L1", env_content)
        self.assertIn("SPARTAN_ADDONS=clawroute", env_content)
        self.assertIn("SPARTAN_HERMES_MODE=free", env_content)
        self.assertIn("COMPOSE_PROJECT_NAME=spartan-gate", env_content)
        self.assertIn("Tier: L1", result.stdout)
        self.assertIn("Addons: clawroute", result.stdout)
        self.assertIn("Hermes mode: free", result.stdout)
        calls = log.read_text(encoding="utf-8")
        compose_file = self.root / "infra" / "compose" / "tiers" / "compose.l1.yml"
        clawroute_file = self.root / "infra" / "compose" / "tiers" / "compose.clawroute.yml"
        self.assertIn(f"docker compose -f {compose_file} -f {clawroute_file} --env-file {env_file} config --quiet", calls)
        self.assertIn(f"docker compose -f {compose_file} -f {clawroute_file} --env-file {env_file} up -d --remove-orphans", calls)
        self.assertIn(f"docker compose -f {compose_file} -f {clawroute_file} --env-file {env_file} up -d --force-recreate hermes", calls)

    def test_l4_tier_uses_private_compose_override_when_present(self):
        bin_dir, log = self.write_docker_stub("1000:1000")
        (self.root / "private" / "env").mkdir(parents=True)
        (self.root / "private" / "env" / "local.env").write_text("SPARTAN_TIER=L4\n", encoding="utf-8")
        private_compose = self.root / "private" / "compose.local.yml"
        private_compose.write_text("services: {}\n", encoding="utf-8")

        result = self.run_sourced_with_path("sg-tier status", bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text(encoding="utf-8")
        self.assertIn(f"-f {self.root / 'infra' / 'compose' / 'compose.yml'}", calls)
        self.assertIn(f"-f {private_compose}", calls)
        self.assertIn(" ps", calls)

    def test_public_helpers_do_not_default_to_old_custom_claw_path(self):
        for path in (
            ROOT / "scripts" / "aliases.sh",
            ROOT / "scripts" / "hermes-profile-new.sh",
        ):
            with self.subTest(path=path):
                self.assertNotIn(".custom_claw", path.read_text(encoding="utf-8"))

    def write_docker_stub(self, runtime_user="1000:1000"):
        bin_dir = self.root / "bin"
        log = self.root / "docker.log"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(
            f"""#!/usr/bin/env bash
printf 'docker' >> {log}
printf ' %s' "$@" >> {log}
printf '\\n' >> {log}
if [[ "$1" == "exec" && "$2" == "-u" && "$3" == "root" ]]; then
  printf '{runtime_user}\\n'
fi
""",
            encoding="utf-8",
        )
        (bin_dir / "docker").chmod(0o755)
        return bin_dir, log

    def run_sourced_with_path(self, command, bin_dir):
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        return subprocess.run(
            ["bash", "-lc", f"source scripts/aliases.sh; {command}"],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_hermes_helpers_exec_as_runtime_user(self):
        bin_dir, log = self.write_docker_stub("1000:1000")

        result = self.run_sourced_with_path("sg_hermes_doctor --quick", bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text(encoding="utf-8")
        self.assertIn("docker exec -u root spartan_gate_hermes sh -lc", calls)
        self.assertIn(
            "docker exec -u 1000:1000 -e HOME=/opt/data -e HERMES_HOME=/opt/data "
            "-e USER=hermes -e LOGNAME=hermes spartan_gate_hermes hermes doctor --quick",
            calls,
        )

    def test_hermes_stdin_helper_keeps_stdin_open(self):
        bin_dir, log = self.write_docker_stub("1000:1000")

        result = self.run_sourced_with_path("sg_hermes_exec_i python3 -", bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "docker exec -i -u 1000:1000 -e HOME=/opt/data -e HERMES_HOME=/opt/data "
            "-e USER=hermes -e LOGNAME=hermes spartan_gate_hermes python3 -",
            log.read_text(encoding="utf-8"),
        )

    def test_hermes_tty_helpers_exec_as_runtime_user(self):
        bin_dir, log = self.write_docker_stub("1234:5678")

        result = self.run_sourced_with_path("sg_hermes_shell -lc true", bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "docker exec -it -u 1234:5678 -e HOME=/opt/data -e HERMES_HOME=/opt/data "
            "-e USER=hermes -e LOGNAME=hermes spartan_gate_hermes bash -lc true",
            log.read_text(encoding="utf-8"),
        )

    def test_hermes_interactive_shell_uses_named_prompt(self):
        bin_dir, log = self.write_docker_stub("1000:1000")

        result = self.run_sourced_with_path("sg_hermes_shell", bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text(encoding="utf-8")
        self.assertIn(
            "docker exec -it -u 1000:1000 -e HOME=/opt/data -e HERMES_HOME=/opt/data "
            "-e USER=hermes -e LOGNAME=hermes spartan_gate_hermes sh -lc",
            calls,
        )
        self.assertIn('PS1="hermes@\\h:\\w\\$ "', calls)

    def test_hermes_meet_uses_runtime_user_and_env(self):
        bin_dir, log = self.write_docker_stub("1234:5678")

        result = self.run_sourced_with_path("sg_hermes_meet stop", bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text(encoding="utf-8")
        self.assertIn("docker exec -u root spartan_gate_hermes sh -lc", calls)
        self.assertIn(
            "docker exec -u root -e HERMES_HOME=/opt/data spartan_gate_hermes sh -lc",
            calls,
        )
        self.assertIn(
            "docker exec -u 1234:5678 -e HOME=/opt/data -e HERMES_HOME=/opt/data "
            "-e USER=hermes -e LOGNAME=hermes spartan_gate_hermes sh -lc",
            calls,
        )
        self.assertNotIn("docker exec -u hermes", calls)


if __name__ == "__main__":
    unittest.main()
