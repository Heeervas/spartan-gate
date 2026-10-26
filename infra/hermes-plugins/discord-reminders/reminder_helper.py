"""Fixed native no_agent script and literal-input contract; no delivery or scheduler."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

import yaml

PLUGIN = "discord-reminders"
SCRIPT = "discord-reminder.py"
INPUTS = ".discord-reminder-inputs"
CAP = 128
MAX_NOTE = 8000
GRACE_SECONDS = 120  # Native ONESHOT_GRACE_SECONDS; verified against supported source.


class Refused(ValueError):
    pass


class Busy(Refused):
    pass


def private_info(info, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise Refused("Reminder installation ownership/mode mismatch.")


def read_file(path, limit=65536, private=True):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        if private:
            private_info(info)
        elif not stat.S_ISREG(info.st_mode):
            raise Refused("Reminder config is not a regular file.")
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise Refused("Reminder input exceeds its limit.")
    return value


def settings(home):
    config = yaml.safe_load(read_file(home / "config.yaml", 262144, private=False))
    plugins = config.get("plugins", {})
    entry = plugins.get("entries", {}).get(PLUGIN, {})
    enabled, disabled = plugins.get("enabled"), plugins.get("disabled", [])
    requesters = entry.get("requesters")
    if (not isinstance(enabled, list) or PLUGIN not in enabled
            or not isinstance(disabled, list) or PLUGIN in disabled
            or entry.get("enabled") is not True
            or not isinstance(entry.get("profile"), str)
            or not isinstance(requesters, list) or not requesters
            or not all(isinstance(item, str) and re.fullmatch(r"[1-9][0-9]{0,19}", item) for item in requesters)):
        raise Refused("Reminder feature is disabled or has no explicit requester scope.")
    return entry


def layout(script):
    script = Path(script)
    private_info(script.lstat())
    if script.name != SCRIPT or script.parent.name != "scripts" or script.parent.is_symlink():
        raise Refused("Reminder helper must be installed inside the profile scripts root.")
    home = script.parent.parent
    for directory in (home, script.parent):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise Refused("Reminder profile/scripts root is not trusted.")
    root = script.parent / INPUTS
    private_info(root.lstat(), directory=True)
    return home, root


@contextmanager
def locked(root):
    fd = os.open(root / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        private_info(os.fstat(fd))
        deadline = time.monotonic() + 2
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy("Reminder operation busy; retry the same request.")
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)  # Also releases on a process crash; no stale lease service.


def request_key(owner, message_id):
    return hashlib.sha256(json.dumps([owner, message_id], ensure_ascii=False).encode()).hexdigest()


def save(path, record):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(record, ensure_ascii=False).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load(path):
    record = json.loads(read_file(path))
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise Refused("Reminder input schema mismatch.")
    return record


def minutes(duration):
    if not isinstance(duration, str) or len(duration) > 128:
        raise Refused("Supply a duration such as 30m or 2h.")
    text = duration.lower().replace(" ", "")
    pieces = list(re.finditer(r"([0-9]+)([mhd])", text))
    if not pieces or "".join(part[0] for part in pieces) != text:
        raise Refused("Supply a duration using m, h or d.")
    values = [int(part[1]) * {"m": 1, "h": 60, "d": 1440}[part[2]] for part in pieces]
    if not all(values) or not 0 < sum(values) <= 365 * 1440:
        raise Refused("Reminder duration must be positive and at most 365 days.")
    return sum(values)


def render(note):
    # Native cron parses stdout as a protocol. Keep user literals inert, not executable directives.
    text = note.replace("@", "@\u200b").replace("[[", "[\u200b[")
    text = re.sub(r"media(?=:)", lambda match: match[0] + "\u200b", text, flags=re.I)
    return f"⏰ Reminder: {text}\nContext: the requesting Discord conversation."


def eligible_owner(record, folder, home, entry):
    owner = record["owner"]
    return (isinstance(owner, list) and len(owner) == 5
            and owner[:2] == [str(home), entry["profile"]]
            and owner[2] in entry["requesters"]
            and request_key(owner, record["message_id"]) == folder.name
            and record.get("state") == "ready"
            and re.fullmatch(r"[a-f0-9]{12}", record.get("job_id", "")))


def emit(script, cwd=None, now=None):
    home, root = layout(script)
    folder = Path.cwd() if cwd is None else Path(cwd)
    if folder.parent != root or not re.fullmatch(r"[a-f0-9]{64}", folder.name):
        raise Refused("Reminder cwd is not a request input directory.")
    with locked(root):
        private_info(folder.lstat(), directory=True)
        record = load(folder / "request.json")
        if not eligible_owner(record, folder, home, settings(home)):
            return False
        note = record["note"]
        due = datetime.fromisoformat(record["due"])
        now = now or datetime.now(timezone.utc)
        if (not isinstance(note, str) or not note.strip() or len(note) > MAX_NOTE
                or due.tzinfo is None or not due <= now <= due + timedelta(seconds=GRACE_SECONDS)):
            return False
        print(render(note), flush=True)
        return True


if __name__ == "__main__":
    try:
        emit(Path(__file__))
    except Busy:
        raise SystemExit(75)  # Native failure/status, not silent successful consumption of a due job.
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        pass  # Revoked, expired, malformed or unavailable input is silent; never an agent fallback.
