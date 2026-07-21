#!/usr/bin/env python3
"""Complete pinned Wrangler OAuth through a container-local loopback callback."""

from __future__ import annotations

import getpass
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request


WRANGLER_PACKAGE = "wrangler@4.45.0"
CALLBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})
CALLBACK_PORT = 8976
CALLBACK_PATH = "/oauth/callback"
AUTH_URL_RE = re.compile(r"https://dash\.cloudflare\.com/oauth2/[A-Za-z0-9?&=._~%/+:-]+")


def validate_callback_url(raw: str) -> str:
    candidate = raw.strip()
    if not candidate or len(candidate) > 8192:
        raise ValueError("callback URL is empty or too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        raise ValueError("callback URL contains control characters")

    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme != "http":
        raise ValueError("callback URL must use http")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("callback URL must not contain userinfo")
    if parsed.hostname not in CALLBACK_HOSTS:
        raise ValueError("callback URL host is not local")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("callback URL port is invalid") from error
    if port != CALLBACK_PORT or parsed.path != CALLBACK_PATH or parsed.fragment:
        raise ValueError("callback URL target is not Wrangler's loopback endpoint")

    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if not query.get("state"):
        raise ValueError("callback URL is missing OAuth state")
    if not query.get("code") and not query.get("error"):
        raise ValueError("callback URL is missing OAuth result")
    return candidate


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def run_oauth() -> int:
    process = subprocess.Popen(
        ["npx", "--yes", WRANGLER_PACKAGE, "login", "--browser=false"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    auth_url = None
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline and process.poll() is None:
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                break
            print(line.rstrip())
            match = AUTH_URL_RE.search(line)
            if match:
                auth_url = match.group(0)
                break
        if not auth_url:
            print("Wrangler did not produce its Cloudflare authorization URL.", file=sys.stderr)
            return 1

        print("\nOpen the authorization URL above in the Camofox noVNC login tab.")
        print("After Cloudflare redirects to localhost:8976, copy the full address-bar URL.")
        callback_raw = getpass.getpass("Paste callback URL (hidden): ")
        try:
            callback_url = validate_callback_url(callback_raw)
        except ValueError as error:
            print(f"Rejected callback URL: {error}", file=sys.stderr)
            return 1
        finally:
            callback_raw = ""

        try:
            with urllib.request.urlopen(callback_url, timeout=10) as response:
                response.read(4096)
        except Exception:
            print("Wrangler's local callback endpoint rejected the OAuth response.", file=sys.stderr)
            return 1
        finally:
            callback_url = ""

        try:
            return_code = process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            print("Wrangler did not finish after the callback.", file=sys.stderr)
            return 1
        if return_code != 0:
            print("Wrangler exited without completing login.", file=sys.stderr)
            return 1
        print("Wrangler OAuth login completed in the persistent Hermes config directory.")
        return 0
    finally:
        _terminate_process_group(process)


def main() -> int:
    try:
        return run_oauth()
    except (OSError, KeyboardInterrupt):
        print("Wrangler OAuth login was interrupted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
