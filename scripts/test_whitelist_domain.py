import os
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "whitelist-domain.sh"
PROXY_ENTRYPOINT = ROOT / "infra" / "outbound-proxy" / "entrypoint.sh"
DNS_ENTRYPOINT = ROOT / "infra" / "dns" / "entrypoint.sh"
NOW = 1_700_000_000


class WhitelistDomainTests(unittest.TestCase):
    def run_helper(self, *args, whitelist=None, now=NOW):
        env = os.environ.copy()
        env["SPARTAN_WHITELIST_FILE"] = str(whitelist or self.whitelist)
        env["SPARTAN_WHITELIST_NOW"] = str(now)
        return subprocess.run(
            ["bash", str(SCRIPT), "--no-restart", *args],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.whitelist = pathlib.Path(self.tmp.name) / "whitelist.private.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def lines(self):
        return self.whitelist.read_text().splitlines()

    def test_adds_permanent_domain(self):
        result = self.run_helper("Example.COM")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Added permanent: example.com", result.stdout)
        self.assertIn("example.com", self.lines())

    def test_adds_temporary_domains_for_supported_units(self):
        cases = [("15m", NOW + 900), ("6h", NOW + 21_600), ("15d", NOW + 1_296_000)]
        for ttl, expected in cases:
            with self.subTest(ttl=ttl):
                self.whitelist.unlink(missing_ok=True)
                result = self.run_helper(ttl, f"{ttl}.example.com")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Added temporary", result.stdout)
                self.assertIn(f"sg-expires-at={expected} sg-ttl={ttl}", self.whitelist.read_text())

    def test_rejects_invalid_duration_like_first_argument(self):
        for ttl in ["0d", "15x", "d15", "1.5h", "-1h", "366d", "999999999999999999d"]:
            with self.subTest(ttl=ttl):
                result = self.run_helper(ttl, "example.com")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid duration", result.stderr)

    def test_supports_comma_separated_domains(self):
        result = self.run_helper("15d", "a.example.com,b.example.com", "https://c.example.com/path")
        self.assertEqual(result.returncode, 0, result.stderr)
        content = self.whitelist.read_text()
        self.assertIn("a.example.com # sg-expires-at=1701296000 sg-ttl=15d", content)
        self.assertIn("b.example.com # sg-expires-at=1701296000 sg-ttl=15d", content)
        self.assertIn("c.example.com # sg-expires-at=1701296000 sg-ttl=15d", content)

    def test_updates_temporary_entry(self):
        self.run_helper("15m", "example.com")
        result = self.run_helper("6h", "example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Updated temporary: example.com", result.stdout)
        self.assertEqual(self.lines().count("example.com # sg-expires-at=1700021600 sg-ttl=6h"), 1)
        self.assertNotIn("sg-ttl=15m", self.whitelist.read_text())

    def test_promotes_temporary_to_permanent(self):
        self.run_helper("15m", "example.com")
        result = self.run_helper("example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Promoted to permanent: example.com", result.stdout)
        self.assertEqual(self.lines().count("example.com"), 1)
        self.assertNotIn("sg-expires-at", self.whitelist.read_text())

    def test_permanent_is_not_degraded_to_temporary(self):
        self.run_helper("example.com")
        result = self.run_helper("15m", "example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Already permanent: example.com", result.stdout)
        self.assertEqual(self.lines().count("example.com"), 1)
        self.assertNotIn("sg-expires-at", self.whitelist.read_text())

    def test_upsert_collapses_duplicate_domain_lines(self):
        self.whitelist.write_text("example.com # sg-expires-at=1700000900 sg-ttl=15m\nexample.com # sg-expires-at=1700021600 sg-ttl=6h\n")
        result = self.run_helper("15d", "example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        matching = [line for line in self.lines() if line.startswith("example.com")]
        self.assertEqual(matching, ["example.com # sg-expires-at=1701296000 sg-ttl=15d"])

    def test_rejects_invalid_ipv4_and_ipv6_explicitly(self):
        self.whitelist.write_text("[2001:db8::1]\n")
        result = self.run_helper("1.2.3.999", "[2001:db8::1]", "valid.example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipping invalid hostname: 1.2.3.999", result.stderr)
        self.assertIn("IPv6 addresses are not supported", result.stderr)
        self.assertIn("valid.example.com", self.lines())
        self.assertNotIn("1.2.3.999", self.whitelist.read_text())
        self.assertEqual(self.lines().count("[2001:db8::1]"), 1)
    def test_repairs_malformed_expiry_marker(self):
        self.whitelist.write_text("example.com # sg-expires-at=garbage\n")
        result = self.run_helper("15m", "example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Updated temporary: example.com", result.stdout)
        self.assertEqual(self.lines(), ["example.com # sg-expires-at=1700000900 sg-ttl=15m"])


    def test_concurrent_updates_do_not_lose_domains(self):
        processes = [
            subprocess.Popen(
                ["bash", str(SCRIPT), "--no-restart", f"host-{index}.example.com"],
                cwd=ROOT,
                env={
                    **os.environ,
                    "SPARTAN_WHITELIST_FILE": str(self.whitelist),
                    "SPARTAN_WHITELIST_NOW": str(NOW),
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for index in range(40)
        ]
        results = [process.communicate() + (process.returncode,) for process in processes]
        self.assertTrue(all(returncode == 0 for _, _, returncode in results), results)
        content = self.whitelist.read_text().splitlines()
        for index in range(40):
            self.assertIn(f"host-{index}.example.com", content)


class WhitelistRuntimeGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.public = self.root / "public.txt"
        self.private = self.root / "private.txt"
        self.combined = self.root / "combined.txt"
        self.public.write_text("public.example.com\n")
        self.private.write_text(
            "future.example.com # sg-expires-at=1700000060 sg-ttl=1m\n"
            "expired.example.com # sg-expires-at=1699999999 sg-ttl=1m\n"
            "invalid-empty.example.com # sg-expires-at=\n"
            "invalid-text.example.com # sg-expires-at=tomorrow\n"
            "invalid-trailing.example.com # sg-expires-at=1700000060oops\n"
            "permanent.example.com\n"
            "https://port.example.com:443/path\n"
            "1.2.3.999\n"
            "[2001:db8::1]\n"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_entrypoint_generate(self, entrypoint, extra_env):
        env = os.environ.copy()
        env.update(extra_env)
        env["SPARTAN_WHITELIST_NOW"] = str(NOW)
        env["SPARTAN_WHITELIST_ENTRYPOINT_TEST"] = "generate"
        return subprocess.run(
            ["sh", str(entrypoint)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_proxy_generation_excludes_expired_domains(self):
        result = self.run_entrypoint_generate(
            PROXY_ENTRYPOINT,
            {
                "SPARTAN_WHITELIST_PUBLIC": str(self.public),
                "SPARTAN_WHITELIST_PRIVATE": str(self.private),
                "SPARTAN_WHITELIST_COMBINED": str(self.combined),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(r"public\.example\.com", result.stdout)
        self.assertIn(r"future\.example\.com", result.stdout)
        self.assertIn(r"permanent\.example\.com", result.stdout)
        self.assertIn(r"port\.example\.com", result.stdout)
        self.assertNotIn(r"expired\.example\.com", result.stdout)
        self.assertNotIn("invalid-empty.example.com", result.stdout)
        self.assertNotIn("invalid-text.example.com", result.stdout)
        self.assertNotIn("invalid-trailing.example.com", result.stdout)
        self.assertNotIn("1.2.3.999", result.stdout)
        self.assertNotIn("2001", result.stdout)

    def test_proxy_generation_emits_anchored_escaped_filters(self):
        self.public.write_text("example.com\napi.example.com\n")
        result = self.run_entrypoint_generate(
            PROXY_ENTRYPOINT,
            {
                "SPARTAN_WHITELIST_PUBLIC": str(self.public),
                "SPARTAN_WHITELIST_PRIVATE": str(self.private),
                "SPARTAN_WHITELIST_COMBINED": str(self.combined),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        filters = self.combined.read_text().splitlines()
        example_filter = next(line for line in filters if "example\\.com" in line and "api\\." not in line)
        translated = example_filter.replace(r"\(", "(").replace(r"\)", ")").replace(r"\?", "?")
        pattern = re.compile(translated)
        for allowed in ["example.com", "sub.example.com", "https://deep.sub.example.com/path"]:
            self.assertRegex(allowed, pattern)
        for denied in ["notexample.com", "exampleXcom", "example.com.attacker.tld"]:
            self.assertNotRegex(denied, pattern)

    def test_dns_generation_excludes_expired_domains(self):
        result = self.run_entrypoint_generate(
            DNS_ENTRYPOINT,
            {
                "SPARTAN_DNS_WHITELIST_PUBLIC": str(self.public),
                "SPARTAN_DNS_WHITELIST_PRIVATE": str(self.private),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        domains = result.stdout.splitlines()
        self.assertIn("public.example.com", domains)
        self.assertIn("future.example.com", domains)
        self.assertIn("permanent.example.com", domains)
        self.assertIn("port.example.com", domains)
        self.assertNotIn("expired.example.com", domains)
        self.assertNotIn("invalid-empty.example.com", result.stdout)
        self.assertNotIn("invalid-text.example.com", result.stdout)
        self.assertNotIn("invalid-trailing.example.com", result.stdout)
        self.assertNotIn("1.2.3.999", result.stdout)
        self.assertNotIn("2001", result.stdout)

    def test_container_images_install_timezone_data(self):
        proxy_dockerfile = (ROOT / "infra" / "outbound-proxy" / "Dockerfile").read_text()
        dns_dockerfile = (ROOT / "infra" / "dns" / "Dockerfile").read_text()
        self.assertIn("tzdata", proxy_dockerfile)
        self.assertIn("tzdata", dns_dockerfile)


if __name__ == "__main__":
    unittest.main()
