#!/usr/bin/env python3
"""Prepare/check the image-pinned, read-only Hermes fast-mode overlay."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "private/compose.hermes-fast-mode.yml"
MANIFEST_TARGET = "/run/spartan-fast-code/manifest.json"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(args):
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} preflight failed")
    return result.stdout


def guard():
    return load("fast_guard", ROOT / "apps/hermes-agent/spartan_fast_mode_guard.py")


def overlay_data():
    config = yaml.safe_load(OVERLAY.read_text())
    service = config["services"]["hermes"]
    image = service["image"]
    env = service["environment"]
    if not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image) or env.get("SPARTAN_FAST_CODE_IMAGE") != image:
        raise ValueError("Fast-mode overlay image pin is invalid")
    mounts = service["volumes"]
    expected = {"/opt/hermes/" + name for name in guard().FILES} | {guard().INIT_PATH, MANIFEST_TARGET}
    if (len(mounts) != len(expected) or {m["target"] for m in mounts} != expected
            or any(m.get("type") != "bind" or m.get("read_only") is not True or m.get("bind", {}).get("create_host_path") is not False for m in mounts)):
        raise ValueError("Fast-mode overlay mounts are invalid")
    return image, env, {m["target"]: Path(m["source"]) for m in mounts}


def check(image_ref):
    if not OVERLAY.exists():
        return
    image, env, mounts = overlay_data()
    if run(["docker", "image", "inspect", "--format", "{{.Id}}", image_ref]).decode().strip() != image:
        raise ValueError("Hermes image changed; regenerate/remove the fast-mode overlay")
    g = guard()
    manifest_path = mounts[MANIFEST_TARGET]
    # Bundles retain the project-relative shape for the same host/container validator.
    bundle = manifest_path.parent
    if any(mounts["/opt/hermes/" + name] != bundle / name for name in g.FILES):
        raise ValueError("Fast-mode overlay source paths are invalid")
    g.validate(bundle, manifest_path, image, env.get("SPARTAN_FAST_CODE_MANIFEST_SHA256", ""), mounts[g.INIT_PATH])


def prepare(container, allow_low_disk=False):
    if shutil.disk_usage(ROOT).free < 5 * 1024 ** 3 and not allow_low_disk:
        raise ValueError("At least 5 GiB free is required; --allow-low-disk requires an explicit operator exception")
    g = guard()
    p = load("fast_patcher", ROOT / "apps/hermes-agent/patches/patch_spartan_fast_mode.py")
    image = run(["docker", "inspect", "--format", "{{.Image}}", container]).decode().strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("Running Hermes image is not immutable")
    original = run(["docker", "exec", container, "cat", "/opt/hermes/hermes_cli/models.py"])
    files = {"hermes_cli/models.py": p.patch_source(original.decode()).encode(),
             "bootstrap/spartan_fast_mode_guard.py": (ROOT / "apps/hermes-agent/spartan_fast_mode_guard.py").read_bytes()}
    anchors = {name: run(["docker", "exec", container, "cat", "/opt/hermes/" + name]) for name in g.ANCHORS}
    init = (ROOT / "apps/hermes-agent/spartan-fast-mode-init.sh").read_bytes()
    manifest = {"schema": 1, "image": image, "files": {n: g.digest(v) for n, v in files.items()},
                "originals": {"hermes_cli/models.py": g.digest(original)},
                "anchors": {n: g.digest(v) for n, v in anchors.items()}, "support": {g.INIT_PATH: g.digest(init)}}
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    bundle = ROOT / "private/runtime/hermes-fast-mode" / (image[7:19] + "-" + g.digest(raw)[:12])
    values = {**files, **anchors, "original/models.py": original, "manifest.json": raw, "init.sh": init}
    mounts = [(bundle / n, "/opt/hermes/" + n) for n in files]
    mounts += [(bundle / "init.sh", g.INIT_PATH), (bundle / "manifest.json", MANIFEST_TARGET)]
    config = {"services": {"hermes": {"image": image, "environment": {
        "SPARTAN_FAST_CODE_IMAGE": image, "SPARTAN_FAST_CODE_MANIFEST_SHA256": g.digest(raw)},
        "volumes": [{"type": "bind", "source": str(s), "target": t, "read_only": True,
                     "bind": {"create_host_path": False}} for s, t in mounts]}}}
    overlay = yaml.safe_dump(config, sort_keys=False).encode()
    if OVERLAY.exists():
        overlay_data()  # Do not overwrite unrelated configuration.
    if bundle.exists() and (bundle.is_symlink() or any(g.bounded_file(bundle / n) != v for n, v in values.items())):
        raise ValueError("Existing fast-mode bundle changed")
    # Validate transformations and the full current Compose stack before publishing.
    with tempfile.TemporaryDirectory(prefix="spartan-fast-preflight-") as tmp:
        staged = Path(tmp) / "bundle"
        staged.mkdir()
        for name, value in values.items():
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        g.validate(staged, staged / "manifest.json", image, g.digest(raw), staged / "init.sh")
        candidate = Path(tmp) / "compose.yml"
        candidate.write_bytes(overlay)
        args = ["docker", "compose", "-f", str(ROOT / "infra/compose/compose.yml")]
        for name in ("compose.local.yml", "compose.hermes-browser-policy.yml"):
            path = ROOT / "private" / name
            if path.exists():
                args += ["-f", str(path)]
        args += ["-f", str(candidate)]
        env_file = ROOT / "private/env/local.env"
        if not env_file.exists():
            env_file = ROOT / ".env"
        if env_file.exists():
            args += ["--env-file", str(env_file)]
        run([*args, "config", "--quiet"])
    bundle.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not bundle.exists():
        staged = Path(tempfile.mkdtemp(prefix=".bundle-", dir=bundle.parent))
        try:
            for name, value in values.items():
                path = staged / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
                path.chmod(0o555 if name == "init.sh" else 0o444)
            os.rename(staged, bundle)
        finally:
            if staged.exists():
                shutil.rmtree(staged)
    fd, temporary = tempfile.mkstemp(prefix=".fast-overlay-", dir=OVERLAY.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(overlay)
        os.replace(temporary, OVERLAY)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print("Prepared image-pinned Hermes fast-mode overlay; no services started")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--container", default="spartan_gate_hermes")
    p.add_argument("--allow-low-disk", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("--image-ref", default="spartan-gate-hermes:latest")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.container, args.allow_low_disk)
    else:
        check(args.image_ref)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never expose private Compose values or credential-bearing stderr.
        raise SystemExit("Hermes fast-mode overlay preflight failed") from None
