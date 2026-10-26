"""Reminder-specific native handoff; the native store owns scheduling and delivery status."""
from functools import partial
import json
import re

from . import reminder_helper as H

PENDING = "Discord reminder awaiting verified literal input"


def destination(owner):
    return f"discord:{owner[3]}" + (f":{owner[4]}" if owner[4] else "")


def verify_job(job, record, folder):
    origin = job.get("origin") or {}
    owner = record["owner"]
    expected = {"name": "discord-reminder:" + folder.name, "script": H.SCRIPT,
                "workdir": str(folder), "deliver": destination(owner), "no_agent": True,
                "failure_deliver": "local", "attach_to_session": False}
    if (any(job.get(key) != value for key, value in expected.items())
            or job.get("schedule", {}).get("kind") != "once"
            or job.get("repeat", {}).get("times") != 1
            or any(job.get(key) for key in ("prompt", "skills", "skill", "model", "provider", "base_url",
                                           "monitor_script", "monitor_url", "context_from"))
            or [origin.get(key) or "" for key in ("platform", "user_id", "chat_id", "thread_id")]
            != ["discord", *owner[2:]]):
        raise H.Refused("Native reminder binding changed; operator inspection required.")
    due = job["schedule"]["run_at"]
    if record.get("due", due) != due:
        raise H.Refused("Reminder due time changed; no automatic rearm is allowed.")
    return due


def receipt(key, record):
    return {"success": True, "reminder_id": key, "item_id": key, "task": record["note"],
            "due_at": record.get("due"), "state": record["state"], "delivery": "not_verified"}


def native_call(ctx, tool, scope, action, **fields):
    from . import guard
    from model_tools import handle_function_call
    _, ids, _, _, enabled, disabled = scope
    guard(ctx, tool, ids["task_id"], ids["session_id"])
    call_ids = {**ids, "tool_call_id": str(ids.get("tool_call_id") or "reminder") + ":" + action}
    result = handle_function_call("cronjob_manage", {"action": action, **fields},
                                  **call_ids, enabled_toolsets=enabled, disabled_toolsets=disabled)
    result = json.loads(result) if isinstance(result, str) else result
    if not isinstance(result, dict) or result.get("success") is not True:
        raise H.Refused("Native cron refused or did not confirm the operation; retry the same request.")
    return result


def request_input(tool, args, ingress):
    from . import CANCEL
    if not isinstance(args, dict):
        raise H.Refused("Supply the reminder fields only.")
    if tool == CANCEL:
        key = args.get("reminder_id")
        if set(args) != {"reminder_id"} or not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
            raise H.Refused("Supply the reminder receipt, not a native job ID or path.")
        return key, None
    if set(args) != {"duration", "note"} or not isinstance(args["note"], str):
        raise H.Refused("Supply one duration and one task, without routing arguments.")
    note = args["note"].strip()
    if not note and getattr(ingress.event, "reply_to_message_id", None):
        note = str(getattr(ingress.event, "reply_to_text", "") or "").strip()
    if not note or len(note) > H.MAX_NOTE or re.fullmatch(r"(?:https?://\S+\s*)+", note):
        raise H.Refused("Supply one reminder task (at most 8000 characters), or explicitly reply to it.")
    record = {"schema": 1, "owner": ingress.owner, "message_id": ingress.message_id,
              "note": note, "minutes": H.minutes(args["duration"]), "state": "preparing"}
    return H.request_key(ingress.owner, ingress.message_id), record


def open_input(root, key, supplied):
    folder = root / key
    if not folder.exists():
        if supplied is None:
            raise H.Refused("Reminder receipt is not available in this profile.")
        if len(list(root.iterdir())) >= H.CAP + 1:  # 128 inputs plus the feature lock.
            raise H.Refused("Reminder input capacity reached; no active grant was evicted.")
        folder.mkdir(mode=0o700)
    H.private_info(folder.lstat(), directory=True)
    path = folder / "request.json"
    if path.is_symlink():
        raise H.Refused("Reminder input must not be a symlink.")
    fresh = not path.exists()
    if fresh:
        if supplied is None:
            raise H.Refused("Reminder receipt has no verified input.")
        H.save(path, supplied)
    record = H.load(path)
    if supplied and any(record[key] != supplied[key] for key in ("owner", "message_id", "note", "minutes")):
        raise H.Refused("This message already has a different reminder; send a new request.")
    return folder, record, fresh


def bound_job(record, folder):
    from cron.jobs import get_job, resolve_job_ref
    return get_job(record["job_id"]) if record.get("job_id") else resolve_job_ref("discord-reminder:" + folder.name)


def initialize(record, folder, fresh, call):
    from cron.jobs import get_job
    job = bound_job(record, folder)
    if fresh and job:
        raise H.Refused("An existing native job has lost its input; operator inspection required.")
    if not job:
        if record.get("job_id"):
            raise H.Refused("Native reminder no longer exists; no automatic recreation allowed.")
        result = call("create", name="discord-reminder:" + folder.name, schedule=f"in {record['minutes']}m", repeat=1,
                      prompt="", no_agent=True, script=H.SCRIPT, workdir=str(folder),
                      deliver=destination(record["owner"]), failure_deliver="local", attach_to_session=False,
                      paused=True, paused_reason=PENDING)
        job = get_job(result["job_id"])
        if not job or job.get("enabled") or job.get("state") != "paused" or job.get("paused_reason") != PENDING:
            raise H.Refused("Native paused-create readback failed; no literal grant was activated.")
    return job


def activate(record, folder, job, call):
    from cron.jobs import get_job
    due = verify_job(job, record, folder)
    if record.get("activated"):
        return  # Never resume an externally paused/terminal job on a normal retry.
    record.update(job_id=job["id"], due=due, state="ready")
    path = folder / "request.json"
    H.save(path, record)
    if H.load(path) != record:
        raise H.Refused("Reminder input readback failed.")
    if job.get("state") == "paused":
        if job.get("paused_reason") != PENDING:
            raise H.Refused("Native reminder was paused externally; no automatic rearm allowed.")
        call("resume", job_id=job["id"])
    elif not job.get("enabled") and not job.get("last_run_at"):
        raise H.Refused("Native reminder is not activatable; operator inspection required.")
    verify_job(get_job(job["id"]), record, folder)
    record["activated"] = True
    H.save(path, record)


def revoke(record, folder, call):
    job = bound_job(record, folder)
    if job:
        verify_job(job, record, folder)
    record["state"] = "revoked"
    H.save(folder / "request.json", record)
    if job:
        call("pause", job_id=job["id"], reason="Discord reminder grant revoked")
    return {**receipt(folder.name, record), "delivery": "may_already_be_in_flight_or_unknown"}


def perform(ctx, tool, args, scope):
    from . import CANCEL
    ingress, _, _, root, _, _ = scope
    key, supplied = request_input(tool, args, ingress)
    call = partial(native_call, ctx, tool, scope)
    with H.locked(root):
        folder, record, fresh = open_input(root, key, supplied)
        if record["owner"] != ingress.owner:
            raise H.Refused("Reminder belongs to another requester/profile/conversation.")
        if tool == CANCEL:
            return revoke(record, folder, call)
        if record["state"] != "revoked":
            activate(record, folder, initialize(record, folder, fresh, call), call)
        return receipt(key, record)
