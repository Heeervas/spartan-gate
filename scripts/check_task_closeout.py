#!/usr/bin/env python3
"""Read-only, task-scoped closeout gate (stdlib and Git only).

CLI: --repo CHECKOUT (--task task.md | --inline receipt.json)
     [--phase ready|closed] (default: closed). Output goes only to stdout.

Contract v1 uses the compact JSON object described below.
A package has exactly one <!-- task-closeout:v1 --> followed immediately by a
```json fence. Evidence paths resolve from the receipt directory. All repository,
resource, archive and reference file paths are absolute; scope paths are literal
repository-relative files/directories (not globs). Unknown fields fail.

Required objects/keys:
 version=1, task_id, owner, repository={common_dir,head},
 outcome={disposition:accepted|rejected|superseded,evidence},
 scope={owned_paths:[path,...],foreign_preserved:[{path,sha256},...],
        no_owned_source_reason:text only when owned_paths is empty},
 writers={state:ceased,authority,observed_at,evidence},
 consumers={state:reconciled,authority,observed_at,evidence},
 recovery={state:verified,evidence}, promotions={state:reconciled,reason},
 resources=[{path,kind:scratch|worktree|native-worktree,owner,
             disposition:removed|retained,evidence,...}], unresolved=[].
 Retention also requires purpose, approved_by (not owner), review_trigger.
 archive={destination,references:[{path,target},...]} for a package, or
 archive={inline:true,reason} for a genuinely small inline task only.
 Evidence is a nonempty list of {path,sha256}, with lowercase SHA-256 of an
 existing regular file. Foreign preserved paths accept the existing {path,sha256}
 form, {path,state:"deleted"} for an absent tracked path, or
 {path,state:"symlink",target} for the exact symlink text (not target contents).
 Unknown ignored files in resource worktrees refuse closure; reconcile them first.
 Removed resources must not contain retained resources or the intended archive.
 Package frontmatter is a flat scalar block with plan_id, owner, status,
 updated_at, superseded_by, closeout. Quoted scalars are accepted.
 A superseded package must name another readable task.md with a different valid
 plan_id in superseded_by, relative to this package. Unknown sentinels and bare
 unverified task IDs fail; no registry is consulted.

Both phases require a registered checkout, exact HEAD/common directory, no
Git locks, classified dirt, clean owned scope (including ignored untracked files),
evidence readback, no unresolved flags, and attributable writer/consumer
assertions. A record/evidence-only package may declare an empty owned_paths list
with no_owned_source_reason; this skips only the owned-scope ignored-file query.
Every dirty source identity still requires classification, and explicitly
identified ignored foreign files remain protected. Observations must have a
timezone, must not be in the future, and must not predate package updated_at.
Ready permits intended resource removal and reference targets not yet present;
archive placement must be vacant (or already this package). Closed also requires
removed resources absent/unregistered, retained resources present, references
resolving to the readable archived task, and matching terminal metadata.

This is cooperative workflow enforcement, not OS isolation or native certainty.
Evidence contents establish acceptance, integration, inventory, native lifecycle
and demonstrated restoration through their attributable issuer; hashes establish
identity only. The tool never runs restoration, cleanup, process inspection or
credential discovery. No arbitrary expiry is applied to evidence. Re-run after
changes and obtain current assertions from the responsible runtime/human.
See test_task_closeout.py for a complete executable synthetic receipt example.
"""

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


MARKER = "<!-- task-closeout:v1 -->"


class Refusal(ValueError):
    """An incomplete or inconsistent closeout claim."""


def require(condition, message):
    if not condition:
        raise Refusal(message)


def fields(value, required, optional=()):
    require(isinstance(value, dict), "expected an object")
    require(set(required) <= value.keys(), f"missing fields: {set(required) - value.keys()}")
    require(not value.keys() - set(required) - set(optional), "unknown fields")
    return value


def text(value):
    require(isinstance(value, str) and bool(value.strip()), "expected nonempty text")
    require(value.strip().lower() not in {"unknown", "pending", "unresolved"}, "unknown assertion")
    return value


def sequence(value, nonempty=False):
    require(isinstance(value, list), "expected a list")
    require(not nonempty or bool(value), "required list is empty")
    return value


def absolute(value):
    path = Path(text(value))
    require(path.is_absolute(), "expected an absolute path")
    return path.resolve()


def relative(value):
    value = text(value)
    path = PurePosixPath(value)
    require(not path.is_absolute() and value == path.as_posix(), "scope path must be relative and normalized")
    require(not set(path.parts) & {"..", ".git"} and path.parts, "unsafe scope path")
    require(not any(char in value for char in "*?[]\\"), "scope paths must be literal")
    return value


def inside(path, parent):
    return path == parent or parent in path.parents


def digest(path):
    require(path.is_file() and not path.is_symlink(), f"not a regular evidence file: {path}")
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def identity(item, base):
    fields(item, ("path", "sha256"))
    claimed = text(item["sha256"])
    require(re.fullmatch(r"[0-9a-f]{64}", claimed), "invalid SHA-256")
    path = base / text(item["path"])
    require(digest(path) == claimed, f"SHA-256 mismatch: {path}")
    return path.resolve()


def evidence(items, base):
    return [identity(item, base) for item in sequence(items, nonempty=True)]


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def metadata(markdown):
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", markdown, re.S)
    require(match, "missing flat package frontmatter")
    result = {}
    for line in match[1].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        require(separator and key not in result, "invalid or duplicate frontmatter key")
        result[key] = value.strip().strip("\"'")
    for key in ("plan_id", "owner", "status", "updated_at", "superseded_by", "closeout"):
        require(key in result, f"missing metadata: {key}")
    date.fromisoformat(result["updated_at"])
    return result


def load_receipt(path, packaged):
    raw = path.read_text(encoding="utf-8")
    meta = None
    if packaged:
        require(raw.count(MARKER) == 1, "package must contain exactly one receipt marker")
        match = re.match(r"\s*```json\s*\r?\n(.*?)\r?\n```(?:\r?\n|$)", raw.split(MARKER)[1], re.S)
        require(match, "receipt marker must be followed by a fenced JSON object")
        meta = metadata(raw)
        raw = match[1]
    return json.loads(raw, object_pairs_hook=unique_object), meta


def git(repo, *arguments):
    # Suppress optional index refresh writes and ambient repository overrides.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    command = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(repo), *arguments]
    result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    require(result.returncode == 0, f"Git readback failed: {arguments[0]}")
    return os.fsdecode(result.stdout)


def registrations(repo):
    entries = {}
    for record in git(repo, "worktree", "list", "--porcelain", "-z").split("\0\0"):
        lines = record.strip("\0").split("\0")
        if lines[0].startswith("worktree "):
            entries[Path(lines[0][9:]).resolve()] = lines
    return entries


def check_checkout(repo, expected_common, registered):
    require(Path(git(repo, "rev-parse", "--show-toplevel").strip()).resolve() == repo,
            "--repo must identify a checkout root")
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve()
    require(common == expected_common, "Git common-repository mismatch")
    require(repo in registered, "checkout is not registered")
    require(not any(line.startswith(("locked", "prunable")) for line in registered[repo]),
            "checkout registration is locked or prunable")
    admin = Path(git(repo, "rev-parse", "--absolute-git-dir").strip())
    locks = list(admin.glob("*.lock")) + list(common.glob("*.lock")) + list((common / "refs").rglob("*.lock"))
    require(not locks, "Git lock is unresolved")
    operations = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-apply", "rebase-merge", "sequencer")
    require(not any((admin / name).exists() for name in operations), "Git operation is unresolved")
    return git(repo, "rev-parse", "HEAD").strip()


def dirty_paths(repo):
    records = iter(git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0"))
    result = set()
    for record in records:
        if not record:
            continue
        result.add(record[3:])
        if "R" in record[:2] or "C" in record[:2]:
            result.add(next(records))
    return result


def foreign_identity(item, repo):
    name = relative(item.get("path"))
    path = repo / name
    require(inside(path.parent.resolve(), repo), "foreign path escapes checkout")
    state = item.get("state", "file")
    if state == "deleted":
        fields(item, ("path", "state"))
        require(not path.exists() and not path.is_symlink(), "foreign deletion is no longer absent")
        tracked = git(repo, "ls-files", "--cached", "-z", "--", f":(literal){name}")
        previous = git(repo, "ls-tree", "--name-only", "-z", "HEAD", "--", name)
        require(name in (tracked + previous).split("\0"), "foreign deletion is not tracked")
    elif state == "symlink":
        fields(item, ("path", "state", "target"))
        require(path.is_symlink(), "foreign symlink is missing or replaced")
        require(os.readlink(path) == text(item["target"]), "foreign symlink target changed")
    else:
        require(state == "file", "unknown foreign identity state")
        identity(item, repo)
    return name


def check_scope(scope, repo):
    fields(scope, ("owned_paths", "foreign_preserved"), ("no_owned_source_reason",))
    owned = [relative(path) for path in sequence(scope["owned_paths"])]
    if owned:
        require("no_owned_source_reason" not in scope,
                "no_owned_source_reason is valid only when owned_paths is empty")
    else:
        text(scope.get("no_owned_source_reason"))
    require(all(inside((repo / path).resolve(), repo) for path in owned), "owned path escapes checkout")
    if owned:
        ignored = git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--",
                      *(f":(literal){path}" for path in owned))
        require(not ignored, "owned scope has ignored untracked data")
    foreign = set()
    for item in sequence(scope["foreign_preserved"]):
        name = relative(item.get("path"))
        require(not any(inside(Path(name), Path(root)) for root in owned), "foreign path overlaps owned scope")
        foreign.add(foreign_identity(item, repo))
    for name in dirty_paths(repo):
        require(not any(inside(Path(name), Path(root)) for root in owned), f"owned scope is dirty: {name}")
        require(name in foreign, f"unknown ownership of dirty path: {name}")
    return [repo / path for path in foreign]


def assertion(value, state, base, meta):
    fields(value, ("state", "authority", "observed_at", "evidence"))
    require(value["state"] == state, f"required assertion state: {state}")
    text(value["authority"])
    observed = datetime.fromisoformat(text(value["observed_at"]))
    require(observed.utcoffset() is not None, "observation requires a timezone")
    require(observed <= datetime.now(timezone.utc), "observation is in the future")
    if meta:
        require(observed.date() >= date.fromisoformat(meta["updated_at"]), "observation predates package update")
    return evidence(value["evidence"], base)


def check_claims(receipt, base, meta):
    require(type(receipt["version"]) is int and receipt["version"] == 1, "unsupported receipt version")
    require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", text(receipt["task_id"])), "invalid task identity")
    text(receipt["owner"])
    outcome = fields(receipt["outcome"], ("disposition", "evidence"))
    require(outcome["disposition"] in {"accepted", "rejected", "superseded"}, "unknown outcome")
    paths = evidence(outcome["evidence"], base)
    paths += assertion(receipt["writers"], "ceased", base, meta)
    paths += assertion(receipt["consumers"], "reconciled", base, meta)
    recovery = fields(receipt["recovery"], ("state", "evidence"))
    require(recovery["state"] == "verified", "recovery/restore is not verified")
    paths += evidence(recovery["evidence"], base)
    promotions = fields(receipt["promotions"], ("state", "reason"))
    require(promotions["state"] == "reconciled", "promotions are not reconciled")
    text(promotions["reason"])
    require(sequence(receipt["unresolved"]) == [], "unresolved flags prevent closure")
    return paths


def check_resource(item, context):
    repo, common, registered, owner, phase, base = context
    fields(item, ("path", "kind", "owner", "disposition", "evidence"),
           ("purpose", "approved_by", "review_trigger"))
    path = absolute(item["path"])
    require(not Path(item["path"]).is_symlink(), "resource path must not be a symlink")
    require(item["owner"] == owner, "unknown or foreign resource ownership")
    require(item["kind"] in {"scratch", "worktree", "native-worktree"}, "unknown resource kind")
    require(item["disposition"] in {"removed", "retained"}, "unknown resource disposition")
    proofs = evidence(item["evidence"], base)
    require(not inside(repo, path), "resource contains the verification checkout")
    if item["disposition"] == "retained":
        for key in ("purpose", "approved_by", "review_trigger"):
            text(item.get(key))
        require(item["approved_by"] != owner, "retention cannot be self-approved")
        require(path.exists(), "retained resource is missing")
    elif phase == "closed":
        require(not path.exists() and not path.is_symlink(), "removed resource still exists")
        require(path not in registered, "removed resource still has Git registration")
    if path in registered or (path / ".git").exists():
        require(item["kind"] != "scratch", "Git workspace cannot be classified as scratch")
        check_checkout(path, common, registered)
        require(not dirty_paths(path), "resource workspace is dirty; disposal/cleanliness unsupported")
        ignored = git(path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
        require(not ignored, "resource workspace has ignored untracked data")
    elif item["kind"] != "scratch" and path.exists():
        raise Refusal("workspace lacks Git registration")
    return path, proofs


def check_resources(receipt, context, protected):
    results = [check_resource(item, context) for item in sequence(receipt["resources"])]
    paths = [path for path, _ in results]
    require(len(paths) == len(set(paths)), "duplicate resource path")
    all_proofs = protected + [proof for _, proofs in results for proof in proofs]
    retained = [path for item, (path, _) in zip(receipt["resources"], results)
                if item["disposition"] == "retained"]
    if "destination" in receipt["archive"]:
        retained.append(absolute(receipt["archive"]["destination"]))
    for item, (path, _) in zip(receipt["resources"], results):
        if item["disposition"] == "removed":
            require(not any(inside(target, path) for target in retained),
                    "resource removal would dispose protected retained resource or archive destination")
            require(not any(inside(proof, path) for proof in all_proofs),
                    "resource removal would dispose foreign data, receipt or required evidence")


def check_metadata(meta, receipt, phase):
    require(meta["plan_id"] == receipt["task_id"] and meta["owner"] == receipt["owner"],
            "receipt and package identity differ")
    require(meta["status"] in {"proposed", "active", "blocked", "review", "done", "rejected", "superseded"},
            "unknown package status")
    require(meta["closeout"] in {"pending", "complete"}, "unknown closeout metadata")
    if phase == "closed":
        terminal = {"accepted": "done", "rejected": "rejected", "superseded": "superseded"}
        require(meta["status"] == terminal[receipt["outcome"]["disposition"]], "package is not terminal for outcome")
        require(meta["closeout"] == "complete", "package closeout is incomplete")
    if receipt["outcome"]["disposition"] == "superseded":
        require(meta["superseded_by"] not in {"", "null", "~"}, "superseded package needs a successor")
    else:
        require(meta["superseded_by"] in {"null", "~"}, "unexpected successor metadata")


def check_successor(meta, source):
    if meta["superseded_by"] in {"null", "~"}:
        return []
    successor = Path(text(meta["superseded_by"]))
    require(not successor.is_absolute(), "successor must be owner-relative")
    target = (source.parent / successor).resolve()
    require(target != source, "successor must be another task")
    require(target.name == "task.md" and target.is_file(), "successor is not a readable task file")
    successor_meta = metadata(target.read_text(encoding="utf-8"))
    successor_id = text(successor_meta["plan_id"])
    require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", successor_id), "invalid successor task identity")
    require(successor_id != meta["plan_id"], "successor task identity must differ")
    text(successor_meta["owner"])
    return [target]


def check_archive(archive, source, receipt, packaged, phase):
    if not packaged:
        fields(archive, ("inline", "reason"))
        require(archive["inline"] is True, "inline archive exemption must be explicit")
        text(archive["reason"])
        return []
    fields(archive, ("destination", "references"))
    target = absolute(archive["destination"])
    require(not Path(archive["destination"]).parent.is_symlink(), "archive destination collision: symlink")
    require(target.name == "task.md" and target.parent.name == receipt["task_id"], "archive identity mismatch")
    require(re.fullmatch(r"\d{4}", target.parent.parent.name) and target.parent.parent.parent.name == "archive",
            "archive must use archive/<year>/<task-id>/task.md")
    if phase == "closed":
        require(source == target and target.is_file(), "package is not at its readable archive destination")
    elif source != target:
        require(not target.parent.exists(), "archive destination collision")
    paths = []
    for reference in sequence(archive["references"], nonempty=True):
        fields(reference, ("path", "target"))
        path = absolute(reference["path"])
        link = text(reference["target"])
        require(link in path.read_text(encoding="utf-8"), "current reference does not contain declared target")
        require((path.parent / link).resolve() == target, "reference resolves to a different task")
        if phase == "closed":
            require((path.parent / link).is_file(), "archive reference is broken")
        paths.append(path)
    return paths


def verify(repo, source, packaged=True, phase="closed"):
    repo, source = Path(repo).resolve(), Path(source).resolve()
    require(phase in {"ready", "closed"}, "unknown verification phase")
    receipt, meta = load_receipt(source, packaged)
    fields(receipt, ("version", "task_id", "owner", "repository", "outcome", "scope", "writers",
                     "consumers", "recovery", "promotions", "resources", "unresolved", "archive"))
    proofs = check_claims(receipt, source.parent, meta)
    repository = fields(receipt["repository"], ("common_dir", "head"))
    common = absolute(repository["common_dir"])
    registered = registrations(repo)
    head = check_checkout(repo, common, registered)
    require(repository["head"] == head, "receipt HEAD does not match checkout")
    foreign = check_scope(receipt["scope"], repo)
    if packaged:
        require(source.name == "task.md", "package entry must be task.md")
        check_metadata(meta, receipt, phase)
        proofs += check_successor(meta, source)
    references = check_archive(receipt["archive"], source, receipt, packaged, phase)
    context = repo, common, registered, receipt["owner"], phase, source.parent
    check_resources(receipt, context, proofs + foreign + references + [source])
    return receipt["task_id"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task", type=Path)
    source.add_argument("--inline", type=Path)
    parser.add_argument("--phase", choices=("ready", "closed"), default="closed")
    args = parser.parse_args(argv)
    try:
        task_id = verify(args.repo, args.task or args.inline, args.task is not None, args.phase)
    except (ValueError, OSError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(f"REFUSED: {error}")
        return 1
    print(f"PASS: {task_id} phase={args.phase} (readback and attributable assertions; no mutation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
