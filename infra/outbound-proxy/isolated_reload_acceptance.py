"""Opt-in native policy reload acceptance; synthetic inputs and internal network only.

Copies the exact candidate helper into a disposable synthetic repository so its
normal Compose/configuration selection runs unchanged. Never loads live env,
profiles, credentials or Compose files. No host publication or public DNS proof.
"""
import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
POLICY_NAME = "whitelist.private.txt"
BASELINE = b"baseline.fixture.test\n"
DOMAIN = "allowed.fixture.test"
DNS_BINARY_SHA256 = "7637d0fd0243b924b13397438677d011bcc5809ea79d24396dc492f5fad8a6e3"
HTTP_PROGRAM = """from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  body=b'OWNED SYNTHETIC POLICY FIXTURE'
  self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
 def log_message(self,*args): pass
ThreadingHTTPServer(('0.0.0.0',8080),Handler).serve_forever()
"""
DNS_PROGRAM = """import json,random,socket,struct,sys
name,server=sys.argv[1:]
query=struct.pack('!HHHHHH',random.randrange(65536),256,1,0,0,1)+b''.join(bytes([len(x)])+x.encode() for x in name.split('.'))+b'\\0'+struct.pack('!HH',1,1)
query+=b'\\0'+struct.pack('!HHIH',41,1232,0,0)
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(35); s.sendto(query,(server,53)); reply=s.recv(4096)
assert reply[:2]==query[:2]
print(json.dumps({'rcode':struct.unpack('!H',reply[2:4])[0]&15,'wireHex':reply.hex()}))
"""
HTTP_PROBE = """import json,urllib.request,urllib.error,sys
opener=urllib.request.build_opener(urllib.request.ProxyHandler({'http':'http://proxy:8888'}))
try:
 with opener.open('http://'+sys.argv[1]+':8080/page',timeout=8) as r: print(json.dumps({'status':r.status,'ownedBody':r.read()==b'OWNED SYNTHETIC POLICY FIXTURE'}))
except urllib.error.HTTPError as e: print(json.dumps({'status':e.code,'ownedBody':False}))
"""


def ede_codes(name, wire_hex):
    """Decode only our exact question plus an EDNS-only error response."""
    data = bytes.fromhex(wire_hex)
    assert struct.unpack('!HHHH', data[4:12]) == (1, 0, 0, 1)
    question = b''.join(bytes([len(x)]) + x.encode() for x in name.split('.')) + b'\0' + struct.pack('!HH', 1, 1)
    assert data[12:12 + len(question)] == question
    offset = 12 + len(question)
    root, kind, _, _, length = struct.unpack('!BHHIH', data[offset:offset + 11])
    assert root == 0 and kind == 41 and offset + 11 + length == len(data)
    offset += 11
    result = []
    while offset < len(data):
        code, size = struct.unpack('!HH', data[offset:offset + 4])
        offset += 4
        assert offset + size <= len(data)
        if code == 15:
            assert size >= 2
            result.append(struct.unpack('!H', data[offset:offset + 2])[0])
        offset += size
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(content)
    path.chmod(0o600)


def binding(source, target):
    return {"type": "bind", "source": str(source), "target": target, "read_only": True,
            "bind": {"create_host_path": False, "selinux": "z"}}


class Acceptance:
    def __init__(self, args):
        self.args = args
        self.name = "sg-policy-test-" + uuid.uuid4().hex[:12]
        self.cache = ROOT / ".agents/isolated-reload" / self.name
        self.repo = self.cache / "repo"
        self.policy = self.repo / "private/policy" / POLICY_NAME
        self.transaction = self.repo / "private/transaction"
        self.compose_file = self.repo / "infra/compose/compose.yml"
        self.env_file = self.repo / "private/env/local.env"
        self.receipt = {"id": self.name, "checks": [], "status": "RUNNING", "publicEgress": False}
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.cache / "home")}
        self.started = False
        self.ids = {}

    def command(self, args, *, check=True, timeout=120, env=None):
        result = subprocess.run(args, cwd=ROOT, env=env or self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError("fixture command failed: " + args[0] + " " + result.stderr.decode(errors="replace")[-2000:])
        return result

    def docker(self, *args, **options):
        return self.command(["docker", *args], **options).stdout.decode().strip()

    def compose(self, *args, **options):
        return self.command(["docker", "compose", "-p", self.name, "-f", str(self.compose_file),
                             "--env-file", str(self.env_file), *args], **options)

    def save(self):
        private_write(self.cache / "receipt.json", json.dumps(self.receipt, indent=2).encode())

    def prepare(self):
        self.cache.mkdir(parents=True, mode=0o700)
        host = self.command(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                            env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())}).stdout.decode().strip()
        if not host.startswith("unix://"):
            raise RuntimeError("only an explicitly observed local Unix Docker endpoint is supported")
        self.env["DOCKER_HOST"] = host
        daemon = self.docker("info", "--format", '{{json .ID}} {{json .Name}} {{json .DockerRootDir}}')
        self.receipt["daemonIdentity"] = daemon
        if shutil.disk_usage(ROOT).free < 5 * 1024 ** 3:
            raise RuntimeError("less than 5 GiB free disk")
        memory = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:'))) * 1024
        if memory < 3 * 1024 ** 3:
            raise RuntimeError("less than 3 GiB memory headroom for small fixtures")
        for directory in (self.transaction, self.policy.parent, self.cache / "home"):
            directory.mkdir(parents=True, mode=0o700)
        private_write(self.policy, BASELINE)
        private_write(self.repo / "infra/outbound-proxy/whitelist.txt", BASELINE)
        self.inputs = {name: digest(ROOT / name) for name in (
            "scripts/whitelist-domain.sh", "infra/outbound-proxy/entrypoint.sh", "infra/dns/entrypoint.sh",
            "infra/outbound-proxy/tinyproxy.conf")}
        for name in self.inputs:
            private_write(self.repo / name, (ROOT / name).read_bytes())
        text = (f"SPARTAN_TIER=L4\nCOMPOSE_PROJECT_NAME={self.name}\n"
                f"SPARTAN_PRIVATE_WHITELIST_PATH={self.policy}\n"
                f"SPARTAN_WHITELIST_TRANSACTION_DIR={self.transaction}\n")
        private_write(self.env_file, text.encode())
        self.build_proxy()
        self.write_compose()
        self.receipt.update({"inputs": self.inputs, "proxyBase": self.base_id, "proxyCandidate": self.proxy_id,
                             "dnsImage": self.args.dns_image, "memoryAvailableBytes": memory})
        self.save()

    def image_metadata(self, reference):
        template = '{"id":{{json .Id}},"descriptor":{{json .Descriptor}},"layers":{{json .RootFS.Layers}}}'
        return json.loads(self.docker("image", "inspect", "--platform", self.args.platform,
                                     "--format", template, reference))

    def verify_base_program(self):
        identity = self.docker("create", "--pull=never", "--platform", self.args.platform, "--network", "none",
            "--label", "com.docker.compose.project=" + self.name, "--label", "com.docker.compose.service=base-program-check",
            "--entrypoint", "/bin/true", self.args.proxy_base_image)
        try:
            self.docker("cp", identity + ":/spartan-entrypoint.sh", str(self.cache / "base-entrypoint.sh"))
            assert digest(self.cache / "base-entrypoint.sh") == self.args.expected_proxy_program_sha256
        finally:
            self.docker("rm", identity)

    def build_proxy(self):
        base = self.image_metadata(self.args.proxy_base_image)
        self.base_id = base["id"]
        assert base["descriptor"]["digest"] == self.args.expected_proxy_manifest
        assert base["descriptor"]["platform"] == {"architecture": "amd64", "os": "linux"}
        self.verify_base_program()
        self.receipt["proxyInputBinding"] = {"immutableIndexReference": self.args.proxy_base_image,
            "selectedManifest": base["descriptor"], "legacyContainerImageField": self.args.legacy_container_image_id,
            "baseProgramSha256": self.args.expected_proxy_program_sha256, "platform": self.args.platform}
        base_tag = self.name + "-base:local"
        self.proxy_tag = self.name + "-proxy:local"
        # Docker's OCI store resolves repository-qualified references here;
        # selected-manifest IDs need not be standalone tag resolver inputs.
        self.docker("image", "tag", self.args.proxy_base_image, base_tag)
        context = self.cache / "build"
        private_write(context / "entrypoint.sh", (ROOT / "infra/outbound-proxy/entrypoint.sh").read_bytes())
        (context / "entrypoint.sh").chmod(0o755)
        private_write(context / "Dockerfile", ("FROM " + base_tag + "\nCOPY entrypoint.sh /spartan-entrypoint.sh\n").encode())
        result = self.command(["docker", "build", "--platform", self.args.platform, "--pull=false", "--network=none", "-t", self.proxy_tag,
                               str(context)], timeout=180)
        private_write(self.cache / "build.log", result.stdout + result.stderr)
        candidate = self.image_metadata(self.proxy_tag)
        old_layers = base["layers"]
        assert candidate["layers"][:len(old_layers)] == old_layers
        self.proxy_id = candidate["id"]

    def write_compose(self):
        common = {"restart": "no", "read_only": True, "pids_limit": 64, "platform": self.args.platform,
                  "networks": ["fixture"], "tmpfs": ["/tmp:size=16777216", "/run:size=8388608"]}
        public = binding(self.repo / "infra/outbound-proxy/whitelist.txt", "/public.txt")
        policy = binding(self.policy.parent, "/run/spartan-policy")
        target = "/run/spartan-policy/" + POLICY_NAME
        proxy = {**common, "image": self.proxy_tag, "mem_limit": "64m",
                 "environment": {"SPARTAN_WHITELIST_PUBLIC": "/public.txt", "SPARTAN_WHITELIST_PRIVATE": target},
                 "volumes": [public, policy, binding(self.repo / "infra/outbound-proxy/tinyproxy.conf", "/etc/tinyproxy/tinyproxy.conf")]}
        dns = {**common, "image": self.args.dns_image, "mem_limit": "32m", "entrypoint": ["sh", "/entrypoint.sh"],
               "environment": {"SPARTAN_DNS_WHITELIST_PUBLIC": "/public.txt", "SPARTAN_DNS_WHITELIST_PRIVATE": target},
               "volumes": [public, policy, binding(self.repo / "infra/dns/entrypoint.sh", "/entrypoint.sh")]}
        probe = {**common, "image": self.args.python_image, "mem_limit": "64m", "user": f"{os.getuid()}:{os.getgid()}",
                 "entrypoint": ["python3", "-c", HTTP_PROGRAM], "volumes": [],
                 "networks": {"fixture": {"aliases": [DOMAIN, "baseline.fixture.test", "expired.fixture.test", "bad.fixture.test"]}}}
        data = {"name": self.name, "services": {"proxy": proxy, "dns": dns, "probe": probe},
                "networks": {"fixture": {"internal": True}}}
        private_write(self.compose_file, json.dumps(data, indent=2).encode())

    def start(self):
        # A renamed inode keeps its staging label: label only the dedicated
        # transaction directory through Docker, never the broad private parent.
        sources = [self.transaction, self.policy.parent, self.repo / "infra/dns/entrypoint.sh",
                   self.repo / "infra/outbound-proxy/whitelist.txt", self.repo / "infra/outbound-proxy/tinyproxy.conf"]
        volumes = [value for index, source in enumerate(sources)
                   for value in ("--volume", str(source) + f":/input-{index}:ro,z")]
        # Compose's rendered selinux:z is not evidence that the engine applied
        # labels. Prepare exact owned fixture sources through Docker's native API.
        self.docker("run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true", "--user", f"{os.getuid()}:{os.getgid()}",
                    "--label", "com.docker.compose.project=" + self.name,
                    *volumes, "--entrypoint", "/bin/true", self.args.dns_image)
        self.compose("config", "--quiet")
        self.started = True
        self.compose("up", "-d", "--no-build", "--pull", "never")
        self.ids = {name: self.compose("ps", "-aq", name).stdout.decode().strip() for name in ("proxy", "dns", "probe")}
        assert all(self.ids.values())
        for identity in self.ids.values():
            item = json.loads(self.docker("inspect", identity))[0]
            assert item["Config"]["Labels"]["com.docker.compose.project"] == self.name
            assert not item["HostConfig"]["PortBindings"]
            assert len(item["NetworkSettings"]["Networks"]) == 1
        networks = json.loads(self.docker("inspect", self.ids["dns"]))[0]["NetworkSettings"]["Networks"]
        self.network = next(iter(networks))
        net = json.loads(self.docker("network", "inspect", self.network))[0]
        assert net["Internal"] is True
        subnet = ipaddress.ip_network(net["IPAM"]["Config"][0]["Subnet"])
        assert subnet.subnet_of(ipaddress.ip_network("172.16.0.0/12")), "fixture client must fit unchanged public Tinyproxy Allow"
        self.dns_ip = networks[self.network]["IPAddress"]
        self.wait(lambda: self.http("baseline.fixture.test")["status"] == 200)
        self.receipt["dnsProgramVersion"] = self.docker("exec", self.ids["dns"], "dnsmasq", "--version")
        self.receipt["dnsProgramPathHash"] = self.docker("exec", self.ids["dns"], "sh", "-c", 'p=$(command -v dnsmasq); sha256sum "$p"')
        assert self.receipt["dnsProgramVersion"].startswith("Dnsmasq version 2.90 ")
        assert self.receipt["dnsProgramPathHash"].split() == [DNS_BINARY_SHA256, "/usr/sbin/dnsmasq"]
        routes = self.docker("exec", self.ids["dns"], "ip", "route")
        assert not any(line.startswith("default ") for line in routes.splitlines())
        self.receipt["fixtureNetwork"] = {"internal": True, "subnet": str(subnet), "dnsRoutes": routes, "hostPorts": []}
        self.before = self.identities()

    def wait(self, predicate, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
            time.sleep(0.1)
        raise RuntimeError("fixture event deadline exceeded")

    def identities(self):
        result = {}
        for name in ("proxy", "dns"):
            item = json.loads(self.docker("inspect", self.ids[name]))[0]
            children = self.docker("exec", self.ids[name], "sh", "-c",
                'read children < /proc/1/task/1/children; for p in $children; do read comm < /proc/$p/comm; read info < /proc/$p/stat; set -- $info; echo "$p $comm ${22}"; done')
            assert len(children.splitlines()) >= 2, "service child and timer must be observable"
            result[name] = {"id": item["Id"], "startedAt": item["State"]["StartedAt"],
                            "restartCount": item["RestartCount"], "image": item["Image"],
                            "supervisorHostPid": item["State"]["Pid"], "supervisorChildren": children}
        return result

    def dns_cache_clears(self):
        logs = self.command(["docker", "logs", self.ids["dns"]])
        return (logs.stdout + logs.stderr).count(b"cleared cache")

    def helper(self, *arguments, check=True):
        before = self.dns_cache_clears()
        env = {**self.env, "SPARTAN_ENV_FILE": str(self.env_file), "SPARTAN_PRIVATE_COMPOSE": str(self.cache / "absent.yml"),
               "SPARTAN_WHITELIST_RELOAD_FALLBACK": "fail"}
        result = self.command(["bash", str(self.repo / "scripts/whitelist-domain.sh"), *arguments], env=env, check=check)
        private_write(self.cache / ("helper-" + str(time.time_ns()) + ".log"), result.stdout + result.stderr)
        if result.returncode == 0 and "--no-reload" not in arguments:
            self.wait(lambda: self.dns_cache_clears() > before)
        return result

    def http(self, domain):
        output = self.docker("exec", "-e", "no_proxy=", "-e", "NO_PROXY=", self.ids["probe"],
                             "python3", "-c", HTTP_PROBE, domain, timeout=15)
        return json.loads(output)

    def dns_selection(self, domain, allowed):
        observations = {key: self.observe_dns(name) for key, name in (
            ("positiveControl", "proof.baseline.fixture.test"),
            ("unlistedControl", "proof.unlisted.fixture.test"),
            ("target", "proof." + domain))}
        expected = {"positiveControl": 23, "unlistedControl": 14, "target": 23 if allowed else 14}
        for key, observation in observations.items():
            code = expected[key]
            assert observation["rcode"] == 5 and observation["edeCodes"] == [code]
            classification = "network error" if code == 23 else "not ready"
            assert any("config error is REFUSED (EDE: " + classification + ")" in line
                       for line in observation["nativeQueryLog"])
            assert not any("forwarded " in line for line in observation["nativeQueryLog"])
        compiled = self.docker("exec", self.ids["dns"], "cat", "/tmp/dnsmasq-whitelist.servers")
        selected = [line for line in compiled.splitlines() if line.startswith(f"server=/{domain}/")]
        assert selected == ([f"server=/{domain}/1.1.1.1", f"server=/{domain}/8.8.8.8"] if allowed else [])
        return {"observations": observations, "compiled": compiled,
                "claim": "controlled native policy selection/revocation only; no packet forwarding or successful resolution"}

    def bound_sources(self):
        expected = digest(self.policy)
        for name in ("proxy", "dns"):
            output = self.docker("exec", "--user", f"{os.getuid()}:{os.getgid()}", self.ids[name],
                                 "sha256sum", "/run/spartan-policy/" + POLICY_NAME)
            assert output.split()[0] == expected, "actual replacement inode unreadable or stale"
        assert set(x.name for x in self.policy.parent.iterdir()) == {POLICY_NAME}

    def publish(self, data, *, verify_bound=True):
        lock = self.transaction / (POLICY_NAME + ".lock")
        with lock.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            fd, name = tempfile.mkstemp(prefix=POLICY_NAME + ".tmp.", dir=self.transaction)
            with os.fdopen(fd, "wb") as candidate:
                candidate.write(data); candidate.flush(); os.fsync(candidate.fileno())
            os.replace(name, self.policy)
        if verify_bound:
            self.bound_sources()

    def effective(self, domain, allowed):
        self.wait(lambda: self.http(domain)["status"] == (200 if allowed else 403))
        if allowed:
            assert self.http(domain)["ownedBody"] is True
        query = self.dns_selection(domain, allowed)
        self.bound_sources()
        assert self.identities() == self.before, "successful reload restarted a supervisor or child"
        return query

    def observe_dns(self, name):
        before = self.command(["docker", "logs", self.ids["dns"]])
        prefix = (before.stdout + before.stderr).decode().splitlines()
        wire = json.loads(self.docker("exec", self.ids["probe"], "python3", "-c", DNS_PROGRAM, name, self.dns_ip, timeout=40))
        after = self.command(["docker", "logs", self.ids["dns"]])
        lines = (after.stdout + after.stderr).decode().splitlines()
        assert lines[:len(prefix)] == prefix
        delta = lines[len(prefix):]
        assert any("query[A] " + name + " " in line for line in delta)
        return {"question": name, **wire, "edeCodes": ede_codes(name, wire["wireHex"]), "nativeQueryLog": delta}

    def admission_diagnostic(self):
        self.receipt["status"] = "DIAGNOSTIC_NOT_ACCEPTANCE"
        phases = []
        for phase in ("before", "allowed", "revoked"):
            if phase == "allowed":
                self.helper("15m", DOMAIN)
                self.wait(lambda: self.http(DOMAIN)["status"] == 200)
            elif phase == "revoked":
                data = self.policy.read_bytes()
                own = [line for line in data.splitlines(keepends=True) if line.split(b"#", 1)[0].strip() == DOMAIN.encode()]
                assert len(own) == 1 and data.replace(own[0], b"", 1) == BASELINE
                self.publish(BASELINE)
                self.helper("baseline.fixture.test")
                self.wait(lambda: self.http(DOMAIN)["status"] == 403)
            self.bound_sources()
            compiled = self.docker("exec", self.ids["dns"], "sh", "-c", 'read value < /tmp/dnsmasq-whitelist.servers; cat /tmp/dnsmasq-whitelist.servers')
            assert (f"server=/{DOMAIN}/1.1.1.1" in compiled) == (phase == "allowed")
            # Same question/options across phases; new transactions, serial
            # responses and real reload cache clearing, no concurrent queries.
            observations = {key: self.observe_dns(name) for key, name in (
                ("positiveControl", "proof.baseline.fixture.test"),
                ("unlistedControl", "proof.unlisted.fixture.test"),
                ("target", "proof." + DOMAIN))}
            assert self.identities() == self.before
            phases.append({"phase": phase, "sourceSha256": digest(self.policy), "compiled": compiled,
                           "observations": observations, "http": self.http(DOMAIN), "stableIdentities": True})
            self.receipt["dnsAdmissionDiagnostic"] = phases
            self.save()
        self.receipt["dnsProgramVersion"] = self.docker("exec", self.ids["dns"], "dnsmasq", "--version")
        self.receipt["dnsProgramPathHash"] = self.docker("exec", self.ids["dns"], "sh", "-c", 'p=$(command -v dnsmasq); sha256sum "$p"')
        self.save()

    def positive_journey(self):
        self.receipt["checks"].append({"before": self.effective(DOMAIN, False)})
        inode, clears = self.policy.stat().st_ino, self.dns_cache_clears()
        self.helper("--no-reload", "source-only.fixture.test")
        assert self.policy.stat().st_ino != inode
        assert self.policy.read_bytes() == BASELINE + b"source-only.fixture.test\n"
        source_only = self.effective("source-only.fixture.test", False)
        assert self.dns_cache_clears() == clears
        # Restore through the same guarded transaction before normal admission.
        # No signals now does not promise that a later refresh cannot adopt it.
        self.publish(BASELINE)
        self.helper("15m", DOMAIN)
        first = self.effective(DOMAIN, True)
        first_source = digest(self.policy)
        self.helper("20m", DOMAIN)
        renewed = self.effective(DOMAIN, True)
        assert digest(self.policy) != first_source, "native helper renewal did not publish new input"
        data = self.policy.read_bytes()
        lines = [line for line in data.splitlines(keepends=True) if line.split(b"#", 1)[0].strip() == DOMAIN.encode()]
        assert len(lines) == 1
        remaining = data.replace(lines[0], b"", 1)
        assert remaining == BASELINE, "guarded revoke must preserve every foreign byte"
        self.publish(remaining)
        self.helper("baseline.fixture.test")
        revoked = self.effective(DOMAIN, False)
        self.receipt["checks"].append({"activeNoReloadAtomicWithMountPreflightAndNoImmediateAdoption": source_only,
            "nativeAtomicAllow": first, "nativeAtomicRenew": renewed,
            "guardedAtomicRevokeAndNativeHelperReload": revoked, "stableIdentities": self.before,
            "dnsClaim": "controlled native policy admission/selection and revoke only; intentionally unrouted, no packet forwarding or external resolution"})
        self.save()

    def inactive_entries(self):
        for domain, marker in (("expired.fixture.test", str(int(time.time()) - 1)),
                               ("bad.fixture.test", "not-a-timestamp"), ("overflow.fixture.test", "999999999999999")):
            self.publish(BASELINE + f"{domain} # sg-expires-at={marker} sg-ttl=15m\n".encode())
            # Exercise actual runtime parsing even if the helper rejects corrupt
            # input before signalling. All signal targets are this owned fixture.
            result = self.helper("baseline.fixture.test", check=False)
            if result.returncode != 0:
                before = self.dns_cache_clears()
                for name in ("proxy", "dns"):
                    self.compose("exec", "-T", name, "sh", "-c", "kill -USR1 1")
                self.wait(lambda: self.dns_cache_clears() > before)
            self.receipt["checks"].append({"inactiveEntryDenied": domain, "dns": self.effective(domain, False)})
        self.publish(BASELINE)
        self.helper("baseline.fixture.test")
        self.save()

    def overflow_fails_closed(self):
        self.publish(b"x" * 262145)
        before = digest(self.policy)
        assert self.helper("baseline.fixture.test", check=False).returncode != 0
        assert digest(self.policy) == before and self.identities() == self.before
        for name in ("proxy", "dns"):
            self.compose("exec", "-T", name, "sh", "-c", "kill -USR1 1", check=False)
        self.wait(lambda: all(not json.loads(self.docker("inspect", self.ids[name]))[0]["State"]["Running"] for name in ("proxy", "dns")))
        states = {name: json.loads(self.docker("inspect", self.ids[name]))[0]["State"] for name in ("proxy", "dns")}
        assert all(value["ExitCode"] != 0 for value in states.values())
        self.receipt["checks"].append({"byteOverflowFailClosed": {name: state["ExitCode"] for name, state in states.items()}})
        self.save()

    def lifecycle(self):
        self.publish(BASELINE, verify_bound=False)
        self.compose("up", "-d", "--no-build", "--pull", "never", "proxy", "dns")
        self.wait(lambda: self.http("baseline.fixture.test")["status"] == 200)
        events = {}
        for name in ("proxy", "dns"):
            daemon = "tinyproxy" if name == "proxy" else "dnsmasq"
            self.wait(lambda: self.command(["docker", "exec", self.ids[name], "sh", "-c", "pidof " + daemon + " >/dev/null"], check=False).returncode == 0)
            identities = self.identities()
            # The source recipe forks its application first, then its timer.
            child = identities[name]["supervisorChildren"].splitlines()[0].split()[0]
            self.docker("exec", self.ids[name], "sh", "-c", 'kill -TERM "$1"', "sh", child, check=False)
            self.wait(lambda: not json.loads(self.docker("inspect", self.ids[name]))[0]["State"]["Running"])
            child_exit = json.loads(self.docker("inspect", self.ids[name]))[0]["State"]["ExitCode"]
            self.compose("up", "-d", "--no-build", "--pull", "never", name)
            self.wait(lambda: self.command(["docker", "exec", self.ids[name], "sh", "-c", "pidof " + daemon + " >/dev/null"], check=False).returncode == 0)
            self.compose("stop", "--timeout", "3", name)
            final = json.loads(self.docker("inspect", self.ids[name]))[0]
            assert not final["State"]["Running"] and final["State"]["ExitCode"] == 143
            assert final["RestartCount"] == 0
            events[name] = {"genuineChildDeathTerminatedWrapper": True, "childExitCode": child_exit,
                            "wrapperTermExit": 143, "automaticRestartCount": 0}
            # Restore this owned role so the other role's identity is readable.
            self.compose("up", "-d", "--no-build", "--pull", "never", name)
            self.wait(lambda: self.command(["docker", "exec", self.ids[name], "sh", "-c", "pidof " + daemon + " >/dev/null"], check=False).returncode == 0)
        self.receipt["checks"].append({"actualNativeChildDeathAndTerm": events})
        self.save()

    def reject_nested_file_bind(self):
        override = self.cache / "nested-file-bind.yml"
        private_write(override, json.dumps({"services": {"dns": {"volumes": [
            binding(self.policy, "/run/spartan-policy/" + POLICY_NAME)]}}}).encode())
        previous = self.ids["dns"]
        self.command(["docker", "compose", "-p", self.name, "-f", str(self.compose_file), "-f", str(override),
                      "--env-file", str(self.env_file), "up", "-d", "--no-build", "--pull", "never", "dns"])
        self.ids["dns"] = self.compose("ps", "-q", "dns").stdout.decode().strip()
        assert self.ids["dns"] != previous
        self.wait(lambda: self.command(["docker", "exec", self.ids["dns"], "pidof", "dnsmasq"], check=False).returncode == 0)
        before = self.identities()
        policy = (self.policy.stat().st_ino, digest(self.policy), self.policy.stat().st_mode)
        staging = sorted(path.name for path in self.transaction.iterdir())
        clears = self.dns_cache_clears()
        for arguments in ((), ("--no-reload",)):
            result = self.helper(*arguments, "15m", "forbidden.fixture.test", check=False)
            assert result.returncode != 0 and b"directory bind" in result.stderr
            assert (self.policy.stat().st_ino, digest(self.policy), self.policy.stat().st_mode) == policy
            assert sorted(path.name for path in self.transaction.iterdir()) == staging
            assert self.dns_cache_clears() == clears and self.identities() == before
        self.receipt["checks"].append({"actualNestedFileBindRejectedBeforeWriteOrSignal": True,
                                       "normalAndActiveNoReload": True, "stableIdentities": before})

    def cleanup(self):
        if self.started:
            owned = self.compose("ps", "-aq").stdout.decode().split()
            states = {}
            for identity in owned:
                item = json.loads(self.docker("inspect", identity))[0]
                assert item["Config"]["Labels"]["com.docker.compose.project"] == self.name
                service = item["Config"]["Labels"]["com.docker.compose.service"]
                states[service] = {key: item["State"].get(key) for key in ("Status", "ExitCode", "Error", "StartedAt")}
                logs = self.command(["docker", "logs", identity], check=False)
                private_write(self.cache / (service + "-final.log"), logs.stdout + logs.stderr)
            self.receipt["fixtureStatesBeforeCleanup"] = states
            self.compose("down", "--timeout", "3")
        self.receipt["cleanup"] = "only exact owned Compose project; no volumes/orphans/global resources removed"
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy-base-image", required=True)
    parser.add_argument("--dns-image", required=True)
    parser.add_argument("--python-image", required=True)
    parser.add_argument("--platform", default="linux/amd64", choices=["linux/amd64"])
    parser.add_argument("--expected-proxy-manifest", required=True)
    parser.add_argument("--expected-proxy-program-sha256", required=True)
    parser.add_argument("--legacy-container-image-id", default="not-used-as-resolver-identity")
    parser.add_argument("--dns-diagnostic", action="store_true", help="collect causal EDNS evidence only; never a full acceptance PASS")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print("Source preflight only. --execute requires explicit synthetic image-build/fixture authority.")
        return
    os.umask(0o077)
    run = Acceptance(args)
    try:
        run.prepare(); run.start()
        if args.dns_diagnostic:
            run.admission_diagnostic()
        else:
            run.positive_journey(); run.inactive_entries(); run.overflow_fails_closed(); run.lifecycle()
            run.reject_nested_file_bind()
        assert run.inputs == {name: digest(ROOT / name) for name in run.inputs}, "candidate changed during acceptance"
        if not args.dns_diagnostic:
            run.receipt["status"] = "PASS"
    except Exception as error:
        run.receipt.update({"status": "FAIL", "error": str(error)})
        raise
    finally:
        run.cleanup()
        print(json.dumps({"status": run.receipt["status"], "receipt": str(run.cache / "receipt.json")}))


if __name__ == "__main__":
    main()
