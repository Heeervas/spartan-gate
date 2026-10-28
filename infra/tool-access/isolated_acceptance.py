"""Bounded, disposable W4 fixtures. No pulls/builds, production mounts or inference.

Default is a source-only readiness check. --execute --resource-approved is a
separate coordinator gate. Import IsolatedFixture for a native Pi journey inside
its context, or use --hold-seconds (max 600) with bounded stdin 'stop' handshake.
All containers/networks are random-named, ownership-labelled and removed in
finally. Receipts and synthetic mode-0600 credentials remain in the owned cache.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAMOFOX_SOURCE_HASH = "c33fb764129fbac0e4dc1839ab095642837d10b76d0a4d0f8edc6428e52a9b24"
SOURCE_FILES = {
    "server.py": "infra/tool-access/server.py",
    "browser.py": "infra/tool-access/browser.py",
    "policy.py": "infra/tool-access/policy.py",
    "transport.py": "infra/tool-access/transport.py",
    "reader.py": "infra/reader/server.py",
    "Caddyfile": "infra/caddy/Caddyfile.tools",
    "patch-server.js": "infra/camofox/patch-server.js",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_file(path, text):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write(text)


def run(command, *, timeout=60, cwd=None, env=None):
    # Do not inherit proxy, Docker context/host, credentials or agent settings.
    result = subprocess.run(command, cwd=cwd, env=env or {"PATH": os.environ["PATH"]},
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command,
                                            output=result.stdout, stderr=result.stderr)
    if command[:2] == ["docker", "logs"]:
        return (result.stdout + result.stderr).strip()
    return result.stdout.strip()


def source_preflight(patched_server_source):
    for relative in SOURCE_FILES.values():
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError("missing/nonregular candidate source: " + relative)
    if digest(Path(patched_server_source)) != CAMOFOX_SOURCE_HASH:
        raise ValueError("extracted Camofox program bytes differ from approved input")
    for name in ("http_fixture.py", "reader_launcher.py", "protocol_client.mjs"):
        if not (FIXTURES / name).is_file():
            raise ValueError("missing fixture source: " + name)


class IsolatedFixture:
    """One owned fixture lifecycle; start is never implicit in construction."""

    def __init__(self, python_image, *, browser_image, caddy_image, patched_server_source):
        for image in (python_image, browser_image, caddy_image):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                raise ValueError("explicit existing program image sha256 ID required")
        self.python_image = python_image
        self.browser_image = browser_image
        self.caddy_image = caddy_image
        self.patched_server_source = Path(patched_server_source).resolve()
        self.id = "sg-tool-test-" + secrets.token_hex(6)
        self.label = "spartan.isolated.acceptance=" + self.id
        self.cache = ROOT / ".agents/isolated-acceptance" / self.id
        self.containers = []
        self.networks = []
        self.names = {}
        self.receipt = {"fixture": self.id, "status": "INCOMPLETE", "checks": [],
                        "browserMode": "TEST-ONLY CAMOFOX_DISABLE_DEFAULT_ADDONS=true; NOT default UBO proof",
                        "readerMode": "actual Reader HTTP + test-only global-host transport; NOT real public network"}

    def docker(self, *args, timeout=60):
        try:
            return run(["docker", *args], timeout=timeout)
        except subprocess.CalledProcessError as error:
            if self.cache.exists():
                # Only owned commands: argv contains paths, never bearer values.
                private_file(self.cache / ("command-failure-" + secrets.token_hex(4) + ".json"),
                    json.dumps({"argv": error.cmd, "exit": error.returncode,
                                "stdout": error.stdout, "stderr": error.stderr}, indent=2))
            raise RuntimeError("owned Docker command failed; private receipt retained") from None

    def prepare(self):
        source_preflight(self.patched_server_source)
        self.cache.mkdir(mode=0o700, parents=True, exist_ok=False)
        for name in ("code", "policy", "client"):
            (self.cache / name).mkdir(mode=0o755)
            (self.cache / name).chmod(0o755)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.host_port = reservation.getsockname()[1]
        code = self.cache / "code"
        for name, relative in SOURCE_FILES.items():
            shutil.copyfile(ROOT / relative, code / name)
        for name in ("http_fixture.py", "reader_launcher.py", "protocol_client.mjs"):
            shutil.copyfile(FIXTURES / name, code / name)
        for copied in code.iterdir():
            copied.chmod(0o644)  # Public program snapshots, never credentials.
        self.receipt["sourceHashes"] = {name: digest(code / name) for name in sorted(os.listdir(code))}
        shutil.copyfile(self.patched_server_source, code / "server.js")
        (code / "server.js").chmod(0o644)
        node = str(ROOT / ".venv/test/bin/node")
        run([node, "patch-server.js"], cwd=code)
        once = digest(code / "server.js")
        run([node, "patch-server.js"], cwd=code)
        if digest(code / "server.js") != once:
            raise AssertionError("Camofox candidate patch is not byte-idempotent")
        self.receipt["browserSource"] = {"originalSha256": CAMOFOX_SOURCE_HASH, "candidateSha256": once}
        self.host_token, self.client_token, master = [secrets.token_hex(32) for _ in range(3)]
        self.policy = {"version": 1, "clients": [
            {"principal": name, "browserUser": self.id + "-" + name,
             "capabilities": ["search", "reader", "browser"],
             "tokenSha256": hashlib.sha256(token.encode()).hexdigest()}
            for name, token in (("fixture-host", self.host_token), ("fixture-container", self.client_token))]}
        private_file(self.cache / "policy/clients.json", json.dumps(self.policy))
        private_file(self.cache / "host.token", self.host_token + "\n")
        private_file(self.cache / "client/client.token", self.client_token + "\n")
        browser_env = {"CAMOFOX_ACCESS_KEY": master, "CAMOFOX_API_KEY": secrets.token_hex(32),
                       "CAMOFOX_ADMIN_KEY": secrets.token_hex(32), "CAMOFOX_DISABLE_DEFAULT_ADDONS": "true",
                       "CAMOFOX_INTERACTIVE": "off", "ENABLE_VNC": "false", "CAMOFOX_GEOIP": "false",
                       "DEBUG": "pw:browser",
                       "CAMOFOX_BIND_HOST": "0.0.0.0", "CAMOFOX_CRASH_REPORT_ENABLED": "false",
                       "CAMOFOX_ADDONS": "", "PROXY_HOST": "", "PROXY_PORT": "", "HTTP_PROXY": "",
                       "HTTPS_PROXY": "", "ALL_PROXY": "", "NO_PROXY": "*", "CAMOFOX_PORT": "9377",
                       "CAMOFOX_PROFILE_DIR": "/root/.camofox/profiles", "MAX_SESSIONS": "4",
                       "MAX_TABS_GLOBAL": "8", "MAX_TABS_PER_SESSION": "4"}
        adapter_env = {"POLICY_FILE": "/policy/clients.json", "SEARCH_URL": "http://fixture:8080",
                       "READER_URL": "http://fixture-reader:3000", "CAMOFOX_URL": "http://fixture-browser:9377",
                       "CAMOFOX_ACCESS_KEY": master,
                       "ALLOWED_HOSTS": f"localhost,localhost:18792,localhost:{self.host_port},127.0.0.1,127.0.0.1:{self.host_port},tool-edge,tool-edge:18792",
                       "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
        for name, values in (("browser.env", browser_env), ("adapter.env", adapter_env)):
            private_file(self.cache / name, "".join(key + "=" + value + "\n" for key, value in values.items()))
        self.save()

    def save(self):
        # Receipts contain only synthetic resource identities and nonsecret hashes.
        (self.cache / "receipt.json").write_text(json.dumps(self.receipt, indent=2) + "\n")

    def network(self, suffix, internal=True):
        name = self.id + "-" + suffix
        self.networks.append(name)  # Retain ownership even if the CLI times out.
        flags = ["--internal"] if internal else []
        self.docker("network", "create", *flags, "--label", self.label, name)
        return name

    def create(self, role, image, network, command, *, memory="128m", extra=(), alias=None):
        name = self.id + "-" + role
        # Native browser matches the supported public recipe; its writable layer
        # and default Docker caps are disposable, not a confinement claim.
        hardening = [] if role == "browser" else ["--cap-drop=ALL", "--read-only"]
        args = ["create", "--pull=never", "--name", name, "--label", self.label,
                "--network", network, "--network-alias", alias or role,
                "--memory", memory, "--memory-swap", memory, "--pids-limit", "256",
                *hardening, "--security-opt", "no-new-privileges", "--security-opt", "label=disable",
                "--no-healthcheck", "--log-driver", "local", "--log-opt", "max-size=2m",
                "--log-opt", "max-file=2", "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
                *extra, image, *command]
        self.containers.append(name)  # Cleanup also covers ambiguous CLI completion.
        self.names[role] = name
        self.docker(*args)
        return name

    def mount(self, source, destination):
        return ["--mount", f"type=bind,src={source},dst={destination},readonly"]

    def start(self):
        source_preflight(self.patched_server_source)
        if shutil.disk_usage(ROOT).free < 5 * 1024 ** 3:
            raise RuntimeError("less than required 5 GiB disk free")
        for image in (self.browser_image, self.caddy_image, self.python_image):
            if self.docker("image", "inspect", "--format", "{{.Id}}", image) != image:
                raise RuntimeError("local program image identity mismatch")
        self.receipt["images"] = {"browser": self.browser_image, "edge": self.caddy_image, "python": self.python_image}
        self.prepare()
        backend, intermediary, ingress = [self.network(name) for name in ("backend", "intermediary", "ingress")]
        self.backend, self.intermediary, self.ingress = backend, intermediary, ingress
        self.publish = self.network("tool-edge-publish", internal=False)
        self.receipt["edgeExposure"] = "Edge has ordinary bridge egress; NOT confined. Publish peer is a test-only negative fixture."
        code = self.cache / "code"
        user = ["--user", f"{os.getuid()}:{os.getgid()}", "--entrypoint", "python3",
                "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "HOME=/tmp/home",
                "--env", "XDG_CACHE_HOME=/tmp/cache", "--env", "XDG_CONFIG_HOME=/tmp/config",
                "--env", "XDG_DATA_HOME=/tmp/data"]
        common = self.mount(code, "/candidate")
        self.create("fixture", self.python_image, backend, ["/candidate/http_fixture.py"],
                    memory="64m", extra=[*user, *common], alias="fixture")
        self.create("reader", self.python_image, backend, ["/candidate/reader_launcher.py"],
                    extra=[*user, *common, "--env", "READER_PORT=3000"], alias="fixture-reader")
        self.create("publish-peer", self.python_image, self.publish, ["/candidate/http_fixture.py"],
                    memory="64m", extra=[*user, *common])
        self.create("browser", self.browser_image, backend, ["python3", "/usr/local/bin/spartan-camofox-supervisor.py", "run"],
                    memory="2g", alias="fixture-browser", extra=[
                        "--entrypoint", "/usr/bin/env", "--shm-size=1g", "--pids-limit", "512",
                        "--env-file", str(self.cache / "browser.env"),
                        "--tmpfs", "/run:rw,nosuid,nodev,size=16m",
                        "--tmpfs", "/root/.camofox:rw,nosuid,nodev,size=128m",
                        "--tmpfs", "/var/lib/spartan-camofox-watchdog:rw,nosuid,nodev,size=4m",
                        *self.mount(code / "server.js", "/app/server.js")])
        adapter = self.create("adapter", self.python_image, backend, ["/candidate/server.py"],
                              memory="256m", extra=[*user, *common, "--env-file", str(self.cache / "adapter.env"),
                              *self.mount(self.cache / "policy", "/policy")], alias="tool-access")
        self.docker("network", "connect", "--alias", "tool-access", intermediary, adapter)
        edge = self.create("edge", self.caddy_image, ingress, ["run", "--config", "/candidate/Caddyfile", "--adapter", "caddyfile"],
                           alias="tool-edge", extra=["--entrypoint", "caddy", "--cap-add", "NET_BIND_SERVICE",
                           "--publish", f"127.0.0.1:{self.host_port}:18792",
                           "--env", "HOME=/tmp/home", "--env", "XDG_CONFIG_HOME=/tmp/config",
                           "--env", "XDG_DATA_HOME=/tmp/data", "--env", "XDG_CACHE_HOME=/tmp/cache",
                           "--tmpfs", "/config:rw,nosuid,nodev,size=4m",
                           "--tmpfs", "/data:rw,nosuid,nodev,size=4m", *common])
        self.docker("network", "connect", intermediary, edge)
        self.docker("network", "connect", self.publish, edge)
        for name in self.containers:
            if name == self.names["browser"]:
                available = next(int(line.split()[1]) * 1024 for line in
                                 Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:"))
                self.receipt["browserStartMemAvailableBytes"] = available
                self.save()
                if available < 4 * 1024 ** 3:
                    raise RuntimeError("less than 4 GiB available before the single 2 GiB browser start")
            self.docker("start", name)
        port = self.docker("port", edge, "18792/tcp")
        if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", port):
            raise RuntimeError("fixture edge binding is not loopback-only")
        bindings = json.loads(self.docker("inspect", "--format", "{{json .HostConfig.PortBindings}}", edge))
        actual = json.loads(self.docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", edge))
        expected_binding = [{"HostIp": "127.0.0.1", "HostPort": str(self.host_port)}]
        self.receipt["publication"] = {"configured": bindings, "actual": actual}
        if bindings.get("18792/tcp") != expected_binding or actual.get("18792/tcp") != expected_binding:
            raise AssertionError("configured and actual edge port must both be loopback-only")
        self.endpoint = "http://" + port
        self.receipt["versions"] = {
            "caddy": self.docker("exec", edge, "caddy", "version"),
            "pythonAiohttp": self.docker("exec", self.names["fixture"], "python3", "-c",
                "import sys,aiohttp,json; print(json.dumps({'python':sys.version.split()[0],'aiohttp':aiohttp.__version__}))"),
            "browserNodePackage": self.docker("exec", self.names["browser"], "node", "-e",
                "console.log(JSON.stringify({node:process.version,camofox:require('/app/package.json').version}))"),
        }
        self.check_topology()
        self.wait_ready()
        self.save()
        return self

    def check_topology(self):
        topology = {}
        expected = {"fixture": {self.backend}, "reader": {self.backend}, "browser": {self.backend},
                    "adapter": {self.backend, self.intermediary},
                    "publish-peer": {self.publish}, "edge": {self.ingress, self.intermediary, self.publish}}
        for role, name in self.names.items():
            networks = json.loads(self.docker("inspect", "--format", "{{json .NetworkSettings.Networks}}", name))
            if set(networks) != expected[role]:
                raise AssertionError("unexpected fixture network membership")
            topology[role] = {network: data["IPAddress"] for network, data in networks.items()}
        network_modes = {}
        for network in self.networks:
            internal = self.docker("network", "inspect", "--format", "{{.Internal}}", network)
            network_modes[network] = internal
            if internal != ("false" if network == self.publish else "true"):
                raise AssertionError("fixture network mode differs from reviewed topology")
        self.receipt["networkInternal"] = network_modes
        self.receipt["topology"] = topology
        self.bypass = [f"http://{ip}:{port}/" for role, ports in {
            "fixture": [8080], "reader": [3000], "browser": [9377], "adapter": [9081],
            "publish-peer": [8080]
        }.items() for ip in topology[role].values() for port in ports]
        self.bypass.append(f"http://{topology['edge'][self.intermediary]}:18792/")
        config = {"baseUrl": "http://tool-edge:18792", "bypass": self.bypass}
        private_file(self.cache / "client/client.json", json.dumps(config))

    def wait_ready(self):
        deadline = time.monotonic() + 120
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            request = urllib.request.Request(self.endpoint + "/v1/workflows", data=b"{}", method="POST",
                      headers={"Host": "localhost", "Authorization": "Bearer " + self.host_token,
                               "Content-Type": "application/json"})
            try:
                with opener.open(request, timeout=5) as response:
                    workflow = json.load(response)["workflowToken"]
                cleanup = urllib.request.Request(self.endpoint + "/v1/workflows", data=b"{}", method="DELETE",
                          headers={**dict(request.header_items()), "X-Spartan-Workflow": workflow})
                with opener.open(cleanup, timeout=5) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError, KeyError):
                time.sleep(1)
        raise RuntimeError("isolated browser/edge did not become ready within 120 seconds")

    def counts(self):
        output = self.docker("exec", self.names["fixture"], "python3", "-c",
                 "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/counts', timeout=3).read().decode())")
        return json.loads(output)

    def protocol(self, mode):
        name = self.create("client-" + mode, self.browser_image, self.ingress,
                 ["/fixture/protocol_client.mjs", mode], memory="256m", extra=[
                 "--entrypoint", "node", "--user", f"{os.getuid()}:{os.getgid()}",
                 "--env", "HOME=/tmp/home", "--env", "XDG_CACHE_HOME=/tmp/cache",
                 "--env", "XDG_CONFIG_HOME=/tmp/config", "--env", "XDG_DATA_HOME=/tmp/data",
                 *self.mount(self.cache / "code/protocol_client.mjs", "/fixture/protocol_client.mjs"),
                 *self.mount(self.cache / "client/client.json", "/fixture/client.json"),
                 *self.mount(self.cache / "client/client.token", "/fixture/client.token")])
        networks = json.loads(self.docker("inspect", "--format", "{{json .NetworkSettings.Networks}}", name))
        if set(networks) != {self.ingress}:
            raise AssertionError("restricted protocol client has extra network membership")
        output = self.docker("start", "--attach", name, timeout=180)
        exit_code = self.docker("inspect", "--format", "{{.State.ExitCode}}", name)
        if exit_code != "0":
            raise RuntimeError("restricted protocol client failed: " + mode)
        receipt = json.loads(output.splitlines()[-1])
        if receipt.get("status") != "PASS":
            raise AssertionError("missing successful protocol receipt")
        self.receipt["checks"].append(receipt)
        self.save()

    def journey(self):
        before = self.counts()
        self.protocol("denied")
        if self.counts() != before:
            raise AssertionError("unauthorized requests caused fixture backend actions")
        self.protocol("journey")
        self.protocol("bypass")
        before = self.counts()
        self.policy["clients"] = [client for client in self.policy["clients"]
                                  if client["principal"] != "fixture-container"]
        policy = self.cache / "policy/clients.json"
        replacement = policy.with_suffix(".new")
        private_file(replacement, json.dumps(self.policy))
        replacement.replace(policy)
        self.protocol("revoked")
        if self.counts() != before:
            raise AssertionError("revoked request caused fixture backend action")
        self.receipt["authZeroAction"] = "missing/wrong/revoked search: unchanged actual fixture counters"
        self.receipt["status"] = "PASS"
        self.save()

    def native(self, pi_package, web_extension):
        pi_package, web_extension = Path(pi_package).resolve(), Path(web_extension).resolve()
        helper = ROOT / "infra/pi/shared_tools_acceptance.mjs"
        extension = ROOT / "infra/pi/extensions/spartan-tools.ts"
        inputs = [helper, extension, *sorted((extension.with_suffix("")).glob("*.mjs")),
                  pi_package / "package.json", pi_package / "dist/index.js", web_extension]
        if any(not path.is_file() for path in inputs):
            raise ValueError("explicit native program/candidate input missing")
        before = {str(path): digest(path) for path in inputs}
        self.receipt["status"] = "INCOMPLETE"
        self.receipt["nativeInputHashesBefore"] = before
        state = self.cache / "native"
        state.mkdir(mode=0o700, exist_ok=False)  # Never repeat first against retained state.
        for name in ("home", "xdg-config", "xdg-data", "xdg-cache", "xdg-state"):
            (state / name).mkdir(mode=0o700)
        connection = self.cache / "connection.json"
        private_file(connection, json.dumps(self.connection()))
        env = {"PATH": os.environ["PATH"], "HOME": str(state / "home"),
               "XDG_CONFIG_HOME": str(state / "xdg-config"), "XDG_DATA_HOME": str(state / "xdg-data"),
               "XDG_CACHE_HOME": str(state / "xdg-cache"), "XDG_STATE_HOME": str(state / "xdg-state"),
               "PI_TEST_PACKAGE_DIR": str(pi_package), "PI_WEB_ACCESS_EXTENSION": str(web_extension),
               "PI_CODING_AGENT_DIR": str(state / "agent"),
               "PI_SPARTAN_TOOLS_CONFIG": str(state / "agent/spartan-tools.json")}
        try:
            for mode in ("first", "resume", "fork", "cleanup"):
                command = [str(ROOT / ".venv/test/bin/node"), str(helper),
                           "--connection", str(connection), "--state-dir", str(state), "--mode", mode]
                try:
                    output = run(command, cwd=ROOT, env=env, timeout=180)
                except subprocess.CalledProcessError as error:
                    private_file(self.cache / ("native-" + mode + "-failure.json"),
                                 json.dumps({"stdout": error.stdout, "stderr": error.stderr}))
                    self.receipt["nativeFailureMode"] = mode
                    raise RuntimeError("native fixture failed; no automatic mutation retry") from None
                receipt = json.loads(output.splitlines()[-1])
                private_file(self.cache / ("native-" + mode + ".json"), json.dumps(receipt, indent=2))
                if receipt.get("status") != "PASS" or receipt.get("mode") != mode:
                    raise AssertionError("native helper receipt did not pass its fixed mode")
                self.receipt["checks"].append(receipt)
                self.save()
        finally:
            after = {str(path): digest(path) for path in inputs}
            self.receipt["nativeInputHashesAfter"] = after
            self.save()
            if before != after:
                raise RuntimeError("native candidate/program input bytes changed during acceptance")
        self.receipt["status"] = "PASS"
        self.save()

    def connection(self):
        return {"event": "fixture_ready", "baseUrl": self.endpoint,
                "credentialFile": str(self.cache / "host.token"), "clientId": "fixture-host",
                "browserUrl": "http://fixture:8080/page",
                "readerUrl": "http://fixture.public.example:8080/page",
                "receipt": str(self.cache / "receipt.json")}

    def close(self):
        errors = []
        for name in reversed(self.containers):
            try:
                state = self.docker("inspect", "--format", "{{json .State}}", name)
                private_file(self.cache / (name + ".state.json"), state + "\n")
            except Exception:
                pass
            try:
                logs = self.docker("logs", "--tail", "2000", name)
                private_file(self.cache / (name + ".log"), logs + "\n")
            except Exception:
                pass  # A stopped/never-started fixture may have no log stream.
            try:
                self.docker("rm", "--force", "--volumes", name, timeout=30)
            except Exception:
                errors.append(name)
        for name in reversed(self.networks):
            try:
                self.docker("network", "rm", name, timeout=30)
            except Exception:
                errors.append(name)
        if self.cache.exists():
            self.receipt["cleanup"] = {"status": "FAIL" if errors else "PASS", "remainingOwnedResources": errors}
            self.save()
        if errors:
            raise RuntimeError("owned fixture cleanup incomplete; see retained receipt")

    def __enter__(self):
        try:
            return self.start()
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_error):
        self.close()


def bounded_hold(seconds):
    if not seconds:
        return
    with selectors.DefaultSelector() as selector:
        selector.register(sys.stdin, selectors.EVENT_READ)
        if selector.select(seconds):
            line = sys.stdin.readline()
            if line.strip() not in {"", "stop", '{"command":"stop"}'}:
                raise ValueError("expected bounded fixture stdin stop handshake")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-image", help="exact existing Python/aiohttp sha256 ID; never pulled")
    parser.add_argument("--browser-image", required=True, help="explicit existing Camofox program sha256 ID")
    parser.add_argument("--caddy-image", required=True, help="explicit existing Caddy program sha256 ID")
    parser.add_argument("--patched-server-source", required=True, help="explicit pinned already-patched image server.js")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resource-approved", action="store_true")
    parser.add_argument("--hold-seconds", type=int, default=0)
    parser.add_argument("--native-pi-package", help="explicit native Pi PROGRAM directory")
    parser.add_argument("--web-access-extension", help="explicit public pi-web-access dist/index.js")
    args = parser.parse_args()
    if not 0 <= args.hold_seconds <= 600:
        parser.error("hold must be bounded from 0 through 600 seconds")
    source_preflight(args.patched_server_source)
    for image in (args.browser_image, args.caddy_image):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            parser.error("explicit program images must be full sha256 IDs")
    if bool(args.native_pi_package) != bool(args.web_access_extension):
        parser.error("native Pi and web-access program paths must be supplied together")
    if args.native_pi_package:
        for path in (Path(args.native_pi_package) / "package.json", Path(args.web_access_extension),
                     ROOT / "infra/pi/shared_tools_acceptance.mjs"):
            if not path.is_file():
                parser.error("explicit native program/helper input missing")
    if not args.execute:
        print(json.dumps({"status": "READY_SOURCE_ONLY", "containersStarted": False,
              "required": "coordinator resource go + exact Python/aiohttp image ID + --execute --resource-approved",
              "browserMode": "TEST-ONLY addons disabled; NOT default UBO proof"}))
        return
    if not args.resource_approved or not args.python_image:
        parser.error("execution requires coordinator resource approval and exact Python/aiohttp image ID")
    def stopped(_signum, _frame):
        raise InterruptedError("owned fixture termination requested")

    signal.signal(signal.SIGTERM, stopped)
    with IsolatedFixture(args.python_image, browser_image=args.browser_image,
                         caddy_image=args.caddy_image, patched_server_source=args.patched_server_source) as fixture:
        fixture.journey()
        if args.native_pi_package:
            fixture.native(args.native_pi_package, args.web_access_extension)
        print(json.dumps(fixture.connection()), flush=True)
        bounded_hold(args.hold_seconds)
    print(json.dumps({"status": "PASS", "receipt": str(fixture.cache / "receipt.json")}))


if __name__ == "__main__":
    main()
