"""Route fixtures only: seal ambient auth and egress before test discovery."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import ipaddress
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch


def loopback(host):
    if host == "localhost":
        return "127.0.0.1"
    try:
        if ipaddress.ip_address(host).is_loopback:
            return host
    except ValueError:
        pass
    raise PermissionError("Route fixtures allow literal loopback connections only")


@contextmanager
def isolated_route_tests():
    # Never derive fixture locations from ambient HOME/TMPDIR or auth variables.
    with tempfile.TemporaryDirectory(prefix="route-tests-", dir="/tmp") as directory, ExitStack() as stack:
        root = Path(directory)
        # PATH alone preserves the wrapper-selected Node 22 for fixture children.
        env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(root / "home"), "CODEX_HOME": str(root / "codex")}
        for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "XDG_CONFIG_DIRS", "XDG_DATA_DIRS"):
            env[key] = str(root / key.lower())
        for path in env.values():
            if path.startswith(directory):
                Path(path).mkdir(mode=0o700)
        stack.enter_context(patch.dict(os.environ, env, clear=True))
        # Some existing fixtures clear os.environ; do not let Path.home fall back
        # to the real passwd home in those tests.
        stack.enter_context(patch.object(Path, "home", return_value=Path(env["HOME"])))
        resolver, connect, connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex

        def resolve(host, *args, **kwargs):
            return resolver(loopback(host), *args, **kwargs)

        def guarded(original):
            def call(sock, address):
                if sock.family in (socket.AF_INET, socket.AF_INET6):
                    address = (loopback(address[0]), *address[1:])
                return original(sock, address)
            return call

        stack.enter_context(patch.object(socket, "getaddrinfo", resolve))
        stack.enter_context(patch.object(socket.socket, "connect", guarded(connect)))
        stack.enter_context(patch.object(socket.socket, "connect_ex", guarded(connect_ex)))
        # Import application code only after both boundaries are in place.
        from spartan_route import codex
        stack.enter_context(patch.object(codex, "CODEX_AUTH_STORE_ROOT", root / "managed-auth"))
        yield root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("-k", help="unittest test-name substring or wildcard filter")
    args = parser.parse_args(argv)
    with isolated_route_tests():
        loader = unittest.TestLoader()
        if args.k:
            loader.testNamePatterns = [args.k if "*" in args.k else f"*{args.k}*"]
        suite = loader.discover(str(Path(__file__).resolve().parent), pattern=args.pattern)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
