import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GogWorkspaceAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bin_dir = self.root / "bin"
        self.log = self.root / "calls.log"
        self.root.joinpath("scripts").mkdir()
        self.root.joinpath("private", "env").mkdir(parents=True)
        self.bin_dir.mkdir()
        shutil.copy2(ROOT / "scripts" / "gog-workspace-auth.sh", self.root / "scripts")
        self.script = self.root / "scripts" / "gog-workspace-auth.sh"
        self.client_secret = self.root / "client_secret.json"
        self.client_secret.write_text("{}\n", encoding="utf-8")
        self.env_file = self.root / "private" / "env" / "local.env"

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_env(self, extra=""):
        self.env_file.write_text(
            "\n".join(
                [
                    f"SPARTAN_GOGCLI_DATA_PATH='{self.root / 'shared' / 'gogcli'}'",
                    "GOG_KEYRING_PASSWORD='super-secret-password'",
                    "GOGCLI_ACCOUNT='person@example.com'",
                    f"GOGCLI_CLIENT_SECRET_PATH='{self.client_secret}'",
                    "GOOGLE_PROJECT_ID='project-id'",
                    "GOOGLE_CLOUD_PROJECT='cloud-project'",
                    extra,
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def write_stub(self, name, content):
        path = self.bin_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def write_stubs(self):
        self.write_stub(
            "gog",
            f"""#!/usr/bin/env bash
{{
  printf 'host-gog'
  printf ' %s' "$@"
  printf '\\n'
  printf 'XDG_CONFIG_HOME=%s\\n' "$XDG_CONFIG_HOME"
}} >> {self.log}
""",
        )
        self.write_stub(
            "docker",
            f"""#!/usr/bin/env bash
if [[ "${{1:-}}" == "inspect" ]]; then
  printf 'true\\n'
  exit 0
fi
if [[ "${{1:-}}" == "exec" && "${{2:-}}" == "-u" && "${{3:-}}" == "root" ]]; then
  printf '1000:1000\\n'
  exit 0
fi
printf 'docker' >> {self.log}
printf ' %s' "$@" >> {self.log}
printf '\\n' >> {self.log}
args=" $* "
last="${{@: -1}}"
state={self.root / 'workspace-authed'}
if [[ "$args" == *" gog login "* ]]; then
  IFS= read -r _gog_callback || true
  exit 0
fi
if [[ "$args" == *" gog auth list --check --json "* ]]; then
  if [[ "${{DOCKER_SCOPE_MODE:-complete}}" == "missing" ]]; then
    printf '%s\\n' '{{"accounts":[{{"email":"person@example.com","scopes":["scope-a"]}}]}}'
  else
    printf '%s\\n' '{{"accounts":[{{"email":"person@example.com","scopes":["scope-a","scope-b","https://www.googleapis.com/auth/analytics.readonly","https://www.googleapis.com/auth/tagmanager.readonly","https://www.googleapis.com/auth/tagmanager.edit.containers","https://www.googleapis.com/auth/tagmanager.edit.containerversions","https://www.googleapis.com/auth/script.projects","https://www.googleapis.com/auth/script.deployments","https://www.googleapis.com/auth/script.processes","https://www.googleapis.com/auth/script.metrics"]}}]}}'
  fi
  exit 0
fi
if [[ "$last" == "--auth-url" ]]; then
  printf 'https://accounts.example/auth\\n'
  exit 0
fi
if [[ "$last" == "--check" ]]; then
  [[ -f "$state" ]]
  exit $?
fi
if [[ "$2" == "--auth-code" || "$last" == "CODE_FROM_BROWSER" ]]; then
  touch "$state"
  exit 0
fi
exit 0
""",
        )

    def run_script(self, input_text="", extra_env=None):
        env = os.environ.copy()
        env["PATH"] = f"{self.bin_dir}:{env['PATH']}"
        env.update(extra_env or {})
        return subprocess.run(
            [self.script],
            cwd=self.root,
            env=env,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_runs_gog_and_workspace_reauth_with_private_env(self):
        self.write_env("GOGCLI_EXTRA_SCOPES='scope-a,scope-b'")
        self.write_stubs()

        result = self.run_script("GOG_CALLBACK\nCODE_FROM_BROWSER\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertIn("docker exec -i -u 1000:1000", calls)
        self.assertIn("gog auth credentials set", calls)
        self.assertNotIn(str(self.client_secret), calls)
        self.assertIn(
            "gog login person@example.com --services "
            "gmail,calendar,chat,classroom,drive,docs,slides,contacts,tasks,"
            "sheets,people,forms,appscript,ads --extra-scopes scope-a,scope-b "
            "--force-consent --manual",
            calls,
        )
        self.assertIn("gog auth list --check --json", calls)
        self.assertIn("gog --account person@example.com gmail labels list", calls)
        self.assertIn(
            "docker exec -u 1000:1000 -e HOME=/opt/data -e HERMES_HOME=/opt/data "
            "-e USER=hermes -e LOGNAME=hermes spartan_gate_hermes",
            calls,
        )
        self.assertIn(" --check", calls)
        self.assertIn(" --auth-url", calls)
        self.assertIn(" --auth-code CODE_FROM_BROWSER", calls)
        self.assertNotIn("host-gog", calls)
        self.assertNotIn("super-secret-password", result.stdout)
        self.assertNotIn("super-secret-password", result.stderr)
        self.assertNotIn("super-secret-password", calls)

    def test_default_login_includes_appscript_service_and_scopes(self):
        self.write_env()
        self.write_stubs()

        result = self.run_script("GOG_CALLBACK\nCODE_FROM_BROWSER\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertIn(
            "gog login person@example.com --services "
            "gmail,calendar,chat,classroom,drive,docs,slides,contacts,tasks,"
            "sheets,people,forms,appscript,ads",
            calls,
        )
        self.assertIn("https://www.googleapis.com/auth/analytics.readonly", calls)
        self.assertIn("https://www.googleapis.com/auth/tagmanager.readonly", calls)
        self.assertIn("https://www.googleapis.com/auth/tagmanager.edit.containers", calls)
        self.assertIn("https://www.googleapis.com/auth/tagmanager.edit.containerversions", calls)
        self.assertIn("https://www.googleapis.com/auth/script.projects", calls)
        self.assertIn("https://www.googleapis.com/auth/script.deployments", calls)
        self.assertIn("https://www.googleapis.com/auth/script.processes", calls)
        self.assertIn("https://www.googleapis.com/auth/script.metrics", calls)

    def test_missing_requested_scope_fails_before_workspace_reauth(self):
        self.write_env("GOGCLI_EXTRA_SCOPES='scope-a,scope-b'")
        self.write_stubs()

        result = self.run_script(
            "GOG_CALLBACK\nCODE_FROM_BROWSER\n",
            extra_env={"DOCKER_SCOPE_MODE": "missing"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing requested OAuth scopes: scope-b", result.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertNotIn("--auth-url", calls)

    def test_missing_required_env_fails_before_external_commands(self):
        self.write_env()
        self.env_file.write_text(
            self.env_file.read_text(encoding="utf-8").replace("GOGCLI_ACCOUNT='person@example.com'\n", ""),
            encoding="utf-8",
        )
        self.write_stubs()

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GOGCLI_ACCOUNT is not set", result.stderr)
        self.assertFalse(self.log.exists())

    def test_hermes_must_already_be_running(self):
        self.write_env()
        self.write_stub("gog", f"#!/usr/bin/env bash\nprintf 'gog %s\\n' \"$*\" >> {self.log}\n")
        self.write_stub(
            "docker",
            f"""#!/usr/bin/env bash
if [[ "${{1:-}}" == "inspect" ]]; then
  printf 'false\\n'
  exit 0
fi
printf 'docker %s\\n' "$*" >> {self.log}
""",
        )

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("spartan_gate_hermes is not running", result.stderr)
        calls = self.log.read_text(encoding="utf-8") if self.log.exists() else ""
        self.assertNotIn("host-gog", calls)
        self.assertNotIn("auth credentials set", calls)
        self.assertNotIn("up -d hermes", calls)


if __name__ == "__main__":
    unittest.main()
