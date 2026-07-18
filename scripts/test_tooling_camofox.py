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

    def test_manual_profile_disables_api_server(self):
        result = self.run_script(
            "manual-profile",
            "--manual",
            "--no-telegram",
            "--no-discord",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        profile_env = (self.data_dir / "profiles" / "manual-profile" / ".env").read_text()
        self.assertIn("API_SERVER_ENABLED=false\n", profile_env)
        self.assertNotIn("API_SERVER_PORT=", profile_env)
        self.assertIn("OPENAI_BASE_URL=${OPENAI_BASE_URL}\n", profile_env)

    def test_gateway_profile_inherits_container_route(self):
        result = self.run_script(
            "gateway-profile",
            "--gateway",
            "--port",
            "8643",
            "--no-telegram",
            "--no-discord",
            "--no-autostart",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        profile_env = (self.data_dir / "profiles" / "gateway-profile" / ".env").read_text()
        self.assertIn("API_SERVER_PORT=8643\n", profile_env)
        self.assertIn("OPENAI_BASE_URL=${OPENAI_BASE_URL}\n", profile_env)


class CamofoxContractTests(unittest.TestCase):
    def test_doctor_only_requires_camofox_path_when_enabled(self):
        source = (ROOT / "scripts" / "doctor.sh").read_text()
        self.assertIn('camofox_enabled=$(env_value "$env_file" CAMOFOX_URL)', source)
        self.assertIn('SPARTAN_CAMOFOX_*) test -n "$camofox_enabled" || continue', source)
        self.assertIn('key ~ /^SPARTAN_CAMOFOX_/', source)

    def test_doctor_knows_spartan_route_package_and_data_path(self):
        source = (ROOT / "scripts" / "doctor.sh").read_text()
        self.assertIn("SPARTAN_ROUTE_DATA_PATH", source)
        self.assertIn("need_dir infra/spartan-route", source)

    def test_doctor_requires_private_websocket_edge_tokens(self):
        source = (ROOT / "scripts" / "doctor.sh").read_text()
        self.assertIn("BROWSERLESS_EDGE_TOKEN CADDY_CAMOFOX_EDGE_TOKEN CADDY_WS_EDGE_TOKEN", source)
        self.assertIn("missing required private edge token", source)
        self.assertIn("invalid private edge token (expected 64 lowercase hex)", source)
        self.assertIn("Caddy edge tokens must differ", source)
        self.assertIn('env_value "$env_file" "$key"', source)

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

    def test_camofox_image_does_not_install_unused_xdotool_resize_dependency(self):
        source = (ROOT / "infra" / "camofox" / "Dockerfile").read_text()
        self.assertNotIn("xdotool", source)

    def test_camofox_viewport_check_runs_from_hermes_over_service_dns(self):
        source = (ROOT / "scripts" / "aliases.sh").read_text()
        start = source.index("sg_camofox_viewport_check()")
        end = source.index("sg_camofox_url()", start)
        helper = source[start:end]
        self.assertIn("hermes python3 -c", helper)
        self.assertIn('BASE = "http://camofox:9377"', helper)
        self.assertNotIn("http://127.0.0.1:9377", helper)
        self.assertIn("CAMOFOX_VIEWPORT_KEY", helper)
        self.assertIn('headers["Authorization"] = f"Bearer {KEY}"', helper)
        self.assertIn('"set-before-navigate"', helper)
        self.assertIn('"set-after-navigate"', helper)
        self.assertIn('"set-then-reload"', helper)
        self.assertIn('"create-with-viewport-observed"', helper)
        self.assertIn('http.server', helper)
        self.assertIn('socketserver.ThreadingTCPServer', helper)
        self.assertIn('DIAGNOSTIC_URL = f"http://hermes:{diagnostic_port}/diagnostic"', helper)
        self.assertIn('create_tab(BOOT_URL)', helper)
        self.assertNotIn('data:text/html', helper)
        self.assertNotIn('about:blank', helper)
        self.assertIn('"GET", f"/tabs/{enc(tab_id)}/screenshot?userId={enc(USER_ID)}&fullPage=false"', helper)
        self.assertIn('"relaunch": True', helper)
        self.assertNotIn('"POST", f"/tabs/{enc(tab_id)}/screenshot"', helper)
        self.assertIn('def screenshot_matches(', helper)
        self.assertIn('"viewportResponse"', helper)
        self.assertIn('"runtimeMatched"', helper)
        self.assertIn('"screenshotMatched"', helper)
        self.assertIn('"matched": runtime_matched and screenshot_matched', helper)
        self.assertIn('f"/sessions/{enc(USER_ID)}"', helper)
        self.assertIn('USER_ID = f"spartan-viewport-smoke-', helper)
        self.assertNotIn('CAMOFOX_USER_ID', helper)
        self.assertNotIn('spartan-camofox-main', helper)
        self.assertNotIn('spartan-camofox-alt', helper)


class AliasHelpTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "aliases.sh", self.root / "scripts")
        shutil.copy2(ROOT / "scripts" / "spartan-route-data.py", self.root / "scripts")
        (self.root / "infra" / "compose" / "tiers").mkdir(parents=True)
        (self.root / "runtime" / "spartan-route").mkdir(parents=True, mode=0o700)

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
            ("sg route -h", "Spartan Route helpers"),
            ("sg-route -h", "Spartan Route helpers"),
            ("sg-hermes -h", "Hermes helpers"),
            ("sg-tmux -h", "Tmux helpers"),
            ("sg-browser -h", "Browser helpers"),
            ("sg-meet -h", "Meet helpers"),
            ("sg-disk -h", "Disk helpers"),
        ):
            with self.subTest(command=command):
                result = self.run_sourced(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_camofox_viewport_helper_is_discoverable(self):
        result = self.run_sourced("sg-browser -h; declare -F sg-camofox-viewport-check sg_camofox_viewport_check")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sg-browser camofox-viewport-check [width height]", result.stdout)
        self.assertIn("sg-camofox-viewport-check", result.stdout)
        self.assertIn("sg_camofox_viewport_check", result.stdout)

    def test_tmux_help_and_navigation_keys_are_discoverable(self):
        result = self.run_sourced(
            "sg-tmux -h; sg-tmux keys; "
            "declare -F sg-tmux sg_tmux sg_tmux_choose sg_tmux_codex sg_tmux_hermes"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sg-tmux attach [name]", result.stdout)
        self.assertIn("sg-tmux hermes <name> [hermes-args...]", result.stdout)
        self.assertIn("Ctrl-b s", result.stdout)
        self.assertIn("Ctrl-b (", result.stdout)
        self.assertIn("sg_tmux_choose", result.stdout)

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

    def test_spartan_route_compose_helper_uses_normal_private_stack(self):
        bin_dir, log = self.write_docker_stub("1000:1000")
        (self.root / "private" / "env").mkdir(parents=True)
        env_file = self.root / "private" / "env" / "local.env"
        env_file.write_text("SPARTAN_ROUTE_EDGE_PORT=18792\n", encoding="utf-8")
        private_compose = self.root / "private" / "compose.local.yml"
        private_compose.write_text("services: {}\n", encoding="utf-8")

        result = self.run_sourced_with_path("sg route up -d", bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text(encoding="utf-8")
        compose_file = self.root / "infra" / "compose" / "compose.yml"
        self.assertIn(
            f"docker compose -f {compose_file} -f {private_compose} --env-file {env_file} up -d",
            calls,
        )
        self.assertNotIn("compose.spartan-route.yml", calls)

    def test_spartan_route_flat_alias_uses_normal_compose(self):
        bin_dir, log = self.write_docker_stub("1000:1000")

        result = self.run_sourced_with_path("sg-route ps", bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text(encoding="utf-8")
        self.assertIn(f"-f {self.root / 'infra' / 'compose' / 'compose.yml'}", calls)
        self.assertNotIn("compose.spartan-route.yml", calls)
        self.assertIn(" ps", calls)

    def test_spartan_route_helper_accepts_underscore_service_alias(self):
        bin_dir, log = self.write_docker_stub("1000:1000")

        result = self.run_sourced_with_path("sg route logs -f spartan_route", bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text(encoding="utf-8")
        self.assertIn(" logs -f spartan-route", calls)
        self.assertNotIn("spartan_route", calls)

    def test_urls_print_l4_spartan_route_port_without_legacy_alias(self):
        (self.root / "private" / "env").mkdir(parents=True)
        (self.root / "private" / "env" / "local.env").write_text(
            "SPARTAN_ROUTE_EDGE_PORT=18792\n",
            encoding="utf-8",
        )

        result = self.run_sourced("sg-urls")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Spartan Route local:     http://localhost:18790", result.stdout)
        self.assertNotIn("Spartan Route alias", result.stdout)
        self.assertNotIn("18792", result.stdout)

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
        bin_dir.mkdir(exist_ok=True)
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

    def write_tmux_stub(self):
        bin_dir = self.root / "bin"
        log = self.root / "tmux.log"
        bin_dir.mkdir(exist_ok=True)
        (bin_dir / "tmux").write_text(
            f"""#!/usr/bin/env bash
printf 'tmux' >> {log}
printf ' <%q>' "$@" >> {log}
printf '\n' >> {log}
case "$1" in
  has-session)
    [[ "${{TMUX_STUB_HAS_SESSION:-0}}" == "1" ]]
    ;;
  list-sessions)
    if [[ "${{TMUX_STUB_EMPTY:-0}}" == "1" ]]; then
      exit 1
    fi
    if [[ "$3" == '#{{session_name}}' ]]; then
      printf 'alpha\nbeta\n'
    else
      printf 'alpha|1|2|2026-07-17 12:00:00\n'
      printf 'beta|0|1|2026-07-17 13:00:00\n'
    fi
    ;;
esac
""",
            encoding="utf-8",
        )
        (bin_dir / "tmux").chmod(0o755)
        (bin_dir / "codex").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        (bin_dir / "codex").chmod(0o755)
        return bin_dir, log

    def run_sourced_with_path(self, command, bin_dir, env_updates=None):
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env.update(env_updates or {})
        return subprocess.run(
            ["bash", "-lc", f"source scripts/aliases.sh; {command}"],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_tmux_choose_uses_native_tree_outside_and_inside_tmux(self):
        bin_dir, log = self.write_tmux_stub()

        outside = self.run_sourced_with_path("sg-tmux choose", bin_dir)
        self.assertEqual(outside.returncode, 0, outside.stderr)
        outside_calls = log.read_text(encoding="utf-8")
        self.assertIn("<attach-session> <-t> <=alpha> <\\;> <choose-tree> <-Zs>", outside_calls)

        log.write_text("", encoding="utf-8")
        inside = self.run_sourced_with_path("sg-tmux choose", bin_dir, {"TMUX": "stub"})
        self.assertEqual(inside.returncode, 0, inside.stderr)
        self.assertIn("tmux <choose-tree> <-Zs>", log.read_text(encoding="utf-8"))

    def test_tmux_list_formats_session_metadata(self):
        bin_dir, _ = self.write_tmux_stub()

        result = self.run_sourced_with_path("sg-tmux list", bin_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NAME", result.stdout)
        self.assertIn("ATTACHED", result.stdout)
        self.assertIn("alpha", result.stdout)
        self.assertIn("2026-07-17 13:00:00", result.stdout)

    def test_tmux_attach_switches_clients_when_already_inside_tmux(self):
        bin_dir, log = self.write_tmux_stub()

        result = self.run_sourced_with_path(
            "sg-tmux attach alpha",
            bin_dir,
            {"TMUX": "stub", "TMUX_STUB_HAS_SESSION": "1"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text(encoding="utf-8")
        self.assertIn("tmux <switch-client> <-t> <=alpha>", calls)
        self.assertNotIn("<attach-session>", calls)

    def test_tmux_codex_uses_current_directory_and_reuses_existing_session(self):
        bin_dir, log = self.write_tmux_stub()

        created = self.run_sourced_with_path("sg-tmux codex codex-work -p deep", bin_dir)
        self.assertEqual(created.returncode, 0, created.stderr)
        calls = log.read_text(encoding="utf-8")
        self.assertIn(f"<new-session> <-s> <codex-work> <-c> <{self.root}>", calls)
        self.assertIn(f"{bin_dir / 'codex'}", calls)
        self.assertIn("-p", calls)
        self.assertIn("deep", calls)

        log.write_text("", encoding="utf-8")
        reused = self.run_sourced_with_path(
            "sg-tmux codex codex-work -p ignored",
            bin_dir,
            {"TMUX_STUB_HAS_SESSION": "1"},
        )
        self.assertEqual(reused.returncode, 0, reused.stderr)
        reuse_calls = log.read_text(encoding="utf-8")
        self.assertIn("tmux <attach-session> <-t> <=codex-work>", reuse_calls)
        self.assertNotIn("<new-session>", reuse_calls)

    def test_tmux_hermes_entry_forwards_exact_cli_shape(self):
        for arguments, expected in (
            ("", "<hermes>"),
            ("chat", "<hermes><chat>"),
            ("-p coach chat", "<hermes><-p><coach><chat>"),
            ("-p coach sessions browse", "<hermes><-p><coach><sessions><browse>"),
        ):
            with self.subTest(arguments=arguments):
                command = (
                    "sg_hermes_shell_command() { printf '<%s>' \"$@\"; }; "
                    f"sg_tmux_session_entry hermes {arguments}"
                )
                result = self.run_sourced(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_tmux_rejects_ambiguous_session_names_before_launch(self):
        bin_dir, log = self.write_tmux_stub()

        result = self.run_sourced_with_path("sg-tmux new bad:name", bin_dir)

        self.assertEqual(result.returncode, 2)
        self.assertIn("Invalid tmux session name", result.stderr)
        self.assertFalse(log.exists())

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
