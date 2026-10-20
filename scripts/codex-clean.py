#!/usr/bin/env python3
"""Remove obsolete host Codex executable releases, never profile data."""

import argparse
from collections import Counter
import os
from pathlib import Path
import re
import shutil
import stat
import sys


RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)-(?:x86_64|aarch64)-unknown-linux-(?:musl|gnu)\Z")


def version(path):
    match = RELEASE.fullmatch(path.name)
    if not match:
        raise ValueError(f"Unrecognized release name: {path}")
    return tuple(map(int, match.groups()))


def discover(home):
    """Only inspect installation metadata, not histories, caches or databases."""
    shared = home / ".local/share/codex-shared/packages/app-server-daemon"
    profiles = [home / ".codex", *home.glob(".codex_*")]
    if os.environ.get("CODEX_HOME"):
        profiles.append(Path(os.environ["CODEX_HOME"]).absolute())
    bases = {shared, *(p / "packages/app-server-daemon" for p in profiles)}
    allowed = {p for p in bases if p.resolve() == p}
    selected, roots = set(), set()
    for base in sorted(bases):
        if not os.path.lexists(base):
            continue
        resolved = base.resolve(strict=True)
        if resolved not in allowed or not resolved.is_relative_to(home):
            raise ValueError(f"Installation resolves outside permitted locations: {base}")
        root = resolved / "releases"
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"Invalid releases directory: {root}")
        current = base / "current"
        if not current.is_symlink():
            raise ValueError(f"Missing current selector: {current}")
        target = current.resolve(strict=True)
        if target.parent != root or not target.is_dir():
            raise ValueError(f"Invalid current selector: {current}")
        version(target)
        selected.add(target)
        roots.add(root)
    return roots, selected


def process_references(proc=Path("/proc")):
    """Read own-user executable, mapped-file, cwd and open-fd metadata only."""
    paths = set()
    excluded_services = 0
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            for link in [process / "exe", process / "cwd"]:
                try:
                    paths.add(os.readlink(link).removesuffix(" (deleted)"))
                except FileNotFoundError:
                    pass
            with (process / "maps").open() as stream:
                for line in stream:
                    fields = line.split(maxsplit=5)
                    if len(fields) == 6 and fields[5].startswith("/"):
                        paths.add(fields[5].strip().removesuffix(" (deleted)"))
            for link in (process / "fd").iterdir():
                try:
                    paths.add(os.readlink(link).removesuffix(" (deleted)"))
                except FileNotFoundError:
                    pass
        except (FileNotFoundError, ProcessLookupError):
            continue  # Process exited during inspection.
        except PermissionError:
            # Non-dumpable login/session services cannot be ptraced by their user.
            # Do not exempt unknown programs or Codex/Node workers on this basis.
            try:
                name = (process / "comm").read_text().strip()
            except (FileNotFoundError, ProcessLookupError):
                continue
            if name not in {"systemd", "(sd-pam)", "sshd"}:
                raise
            excluded_services += 1
    if excluded_services:
        print(f"Process check excludes {excluded_services} unreadable system/login "
              "services; other-user/container references are not checked.", file=sys.stderr)
    return paths


def inventory(paths):
    """Snapshot metadata without following links; count all hardlinks as a set."""
    entries = {}
    for root in paths:
        if root.resolve() != root:
            raise ValueError(f"Symlinked release: {root}")
        device = root.stat().st_dev
        pending = [root]
        while pending:
            path = pending.pop()
            info = path.lstat()
            if (info.st_dev != device or info.st_uid != os.getuid()
                    or path.is_mount()):
                raise ValueError(f"Foreign filesystem or owner: {path}")
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
                    or stat.S_ISLNK(info.st_mode)):
                raise ValueError(f"Unexpected file type: {path}")
            entries[str(path)] = (info.st_dev, info.st_ino, info.st_mode,
                                  info.st_size, info.st_mtime_ns, info.st_nlink,
                                  info.st_blocks * 512)
            if stat.S_ISDIR(info.st_mode):
                pending.extend(path.iterdir())
    return entries


def recoverable(entries):
    references = Counter(value[:2] for value in entries.values())
    unique = {value[:2]: value for value in entries.values()}
    return sum(v[6] for key, v in unique.items()
               if stat.S_ISDIR(v[2]) or references[key] == v[5])


def plan(home, keep=1):
    roots, selected = discover(home)
    references = process_references()
    candidates = []
    for root in sorted(roots):
        releases = sorted(root.iterdir(), key=version, reverse=True)
        if any(p.is_symlink() or not p.is_dir() for p in releases):
            raise ValueError(f"Unexpected release entry in {root}")
        protected = selected | {p for p in releases if any(
            ref == str(p) or ref.startswith(str(p) + "/") for ref in references)}
        # Never discard a downloaded build newer than a selected release.
        ceiling = max(version(p) for p in selected if p.parent == root)
        old = [p for p in releases if p not in protected and version(p) < ceiling]
        candidates.extend(old[keep:])
    return candidates, inventory(candidates)


def apply(home, candidates, snapshot, keep=1):
    """Finish every validation before the first deletion; never expand the set."""
    fresh, _ = plan(home, keep)
    if not set(candidates).issubset(fresh) or inventory(candidates) != snapshot:
        raise ValueError("Candidates or active selections changed; rerun the preview")
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError("This Python lacks symlink-safe directory deletion")
    before = shutil.disk_usage(home).free
    for path in candidates:
        shutil.rmtree(path)
        print(f"Removed {path}", flush=True)
    after = shutil.disk_usage(home).free
    print(f"Net available-space change: {(after - before) / 1024**2:.1f} MiB; "
          f"available: {after / 1024**3:.2f} GiB (includes concurrent writes)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="preview only")
    mode.add_argument("--yes", action="store_true", help="delete without prompting")
    parser.add_argument("--keep", type=int, default=1,
                        help="extra older unused releases to retain per installation (default: 1)")
    args = parser.parse_args()
    if args.keep < 0:
        parser.error("--keep must be nonnegative")
    if os.geteuid() == 0:
        parser.error("run as your normal user, without sudo")
    home = Path.home().resolve()
    try:
        candidates, snapshot = plan(home, args.keep)
        for path in candidates:
            print(f"Candidate: {path}")
        print(f"Estimated physical recovery: {recoverable(snapshot) / 1024**2:.1f} MiB")
        if not candidates or args.dry_run:
            return 0
        if not args.yes:
            if not sys.stdin.isatty():
                print("Preview only. Use --yes to delete, or run interactively.")
                return 0
            if input("Remove these executable releases? [y/N] ").strip().lower() != "y":
                return 0
        apply(home, candidates, snapshot, args.keep)
        return 0
    except (OSError, ValueError) as exc:
        print(f"Codex cleanup stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
