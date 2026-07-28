import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TirithReleaseWhitelistTests(unittest.TestCase):
    def test_release_asset_host_is_exact_and_narrow(self):
        entries = {
            line.strip()
            for line in (ROOT / "infra/outbound-proxy/whitelist.txt").read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("release-assets.githubusercontent.com", entries)
        self.assertNotIn("githubusercontent.com", entries)
        self.assertFalse(any("*" in entry for entry in entries))

    def test_generated_proxy_and_dns_policies_match_only_the_exact_host(self):
        host = "release-assets.githubusercontent.com"
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            public = temp / "public.txt"
            private = temp / "private.txt"
            proxy_filter = temp / "proxy.filter"
            dns_dir = temp / "dns"
            public.write_text(f"{host}\n", encoding="utf-8")
            private.write_text("", encoding="utf-8")
            dns_dir.mkdir()

            proxy_env = os.environ.copy()
            proxy_env.update({
                "SPARTAN_WHITELIST_ENTRYPOINT_TEST": "generate",
                "SPARTAN_WHITELIST_PUBLIC": str(public),
                "SPARTAN_WHITELIST_PRIVATE": str(private),
                "SPARTAN_WHITELIST_COMBINED": str(proxy_filter),
            })
            proxy = subprocess.run(
                ["sh", str(ROOT / "infra/outbound-proxy/entrypoint.sh")],
                cwd=ROOT, env=proxy_env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(proxy.returncode, 0, proxy.stderr)

            urls = {
                f"https://{host}/file": True,
                f"https://sub.{host}/file": True,
                f"https://{host}.evil.example/file": False,
                f"https://evil{host}/file": False,
            }
            for url, expected in urls.items():
                result = subprocess.run(
                    ["grep", "-q", "-f", str(proxy_filter)],
                    input=f"{url}\n", text=True, check=False,
                )
                self.assertEqual(result.returncode == 0, expected, url)

            dns_env = os.environ.copy()
            dns_env.update({
                "SPARTAN_WHITELIST_ENTRYPOINT_TEST": "generate",
                "SPARTAN_DNS_WHITELIST_PUBLIC": str(public),
                "SPARTAN_DNS_WHITELIST_PRIVATE": str(private),
                "SPARTAN_WHITELIST_TEST_ARTIFACT_DIR": str(dns_dir),
            })
            dns = subprocess.run(
                ["sh", str(ROOT / "infra/dns/entrypoint.sh")],
                cwd=ROOT, env=dns_env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(dns.returncode, 0, dns.stderr)

            domains = (dns_dir / "domains.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(domains, [host])
            servers = (dns_dir / "servers.conf").read_text(encoding="utf-8")
            self.assertIn(f"server=/{host}/1.1.1.1", servers)
            self.assertIn(f"server=/{host}/8.8.8.8", servers)
            self.assertNotIn(f"server=/{host}.evil.example/", servers)


if __name__ == "__main__":
    unittest.main()
