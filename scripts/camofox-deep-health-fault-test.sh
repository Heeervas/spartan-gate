#!/usr/bin/env bash
set -eo pipefail

container="$CAMOFOX_CONTAINER_ID"
[ -n "$container" ] || {
    printf 'CAMOFOX_CONTAINER_ID is required\n' >&2
    exit 2
}
unhealthy_timeout="$CAMOFOX_FAULT_UNHEALTHY_TIMEOUT_SECONDS"
recovery_timeout="$CAMOFOX_FAULT_RECOVERY_TIMEOUT_SECONDS"
poll_seconds="$CAMOFOX_FAULT_POLL_SECONDS"
[ -n "$unhealthy_timeout" ] || unhealthy_timeout=180
[ -n "$recovery_timeout" ] || recovery_timeout=300
[ -n "$poll_seconds" ] || poll_seconds=2
[[ "$unhealthy_timeout" =~ ^[1-9][0-9]{0,3}$ ]] || exit 2
[[ "$recovery_timeout" =~ ^[1-9][0-9]{0,3}$ ]] || exit 2
[[ "$poll_seconds" =~ ^[1-9][0-9]?$ ]] || exit 2

health_state() {
    docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container"
}

restart_count() {
    docker inspect --format '{{.RestartCount}}' "$container"
}

browser_pid() {
    docker exec "$container" python3 -c '
import pathlib
matches = []
for entry in pathlib.Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    try:
        comm = (entry / "comm").read_text().strip()
        stat = (entry / "stat").read_text()
        process_fields = stat[stat.rfind(")") + 2:].split()
        state = process_fields[0]
        ppid = int(process_fields[1])
        parent_comm = (pathlib.Path("/proc") / str(ppid) / "comm").read_text().strip()
    except (IndexError, OSError, ValueError):
        continue
    if comm == "camoufox-bin" and parent_comm == "node" and state not in {"Z", "X"}:
        matches.append(int(entry.name))
if not matches:
    raise SystemExit(1)
print(min(matches))
'
}

[ "$(health_state)" = healthy ] || {
    printf 'fault_test=false reason=initial_health\n' >&2
    exit 1
}
before_restarts=$(restart_count)
stopped_pid=$(browser_pid)
[ "$stopped_pid" -gt 1 ] || exit 1

resume_if_alive() {
    docker exec "$container" kill -CONT "$stopped_pid" >/dev/null 2>&1 || true
}
trap resume_if_alive EXIT HUP INT TERM

docker exec "$container" kill -STOP "$stopped_pid"

unhealthy_seen=false
deadline=$((SECONDS + unhealthy_timeout))
while [ "$SECONDS" -lt "$deadline" ]; do
    state=$(health_state)
    if [ "$state" = unhealthy ]; then
        unhealthy_seen=true
        break
    fi
    sleep "$poll_seconds"
done
[ "$unhealthy_seen" = true ] || {
    printf 'fault_test=false reason=unhealthy_not_observed\n' >&2
    exit 1
}

deadline=$((SECONDS + recovery_timeout))
while [ "$SECONDS" -lt "$deadline" ]; do
    if [ "$(health_state)" = healthy ]; then
        replacement_pid=$(browser_pid 2>/dev/null || true)
        if [ -z "$replacement_pid" ]; then
            sleep "$poll_seconds"
            continue
        fi
        after_restarts=$(restart_count)
        if [ "$replacement_pid" = "$stopped_pid" ]; then
            sleep "$poll_seconds"
            continue
        fi
        [ "$after_restarts" = "$before_restarts" ] || {
            printf 'fault_test=false reason=container_restarted\n' >&2
            exit 1
        }
        trap - EXIT HUP INT TERM
        printf 'fault_test=true unhealthy_observed=true recovered=true restart_count_delta=0\n'
        exit 0
    fi
    sleep "$poll_seconds"
done

printf 'fault_test=false reason=recovery_timeout\n' >&2
exit 1
