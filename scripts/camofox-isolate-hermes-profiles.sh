#!/bin/sh
set -e

action=apply
if [ "$#" -gt 0 ]; then
    action=$1
    shift
fi

data_root=/opt/data
if [ -n "$SPARTAN_CAMOFOX_DATA_ROOT" ]; then
    data_root=$SPARTAN_CAMOFOX_DATA_ROOT
fi
hermes_bin=hermes
if [ -n "$SPARTAN_CAMOFOX_HERMES_BIN" ]; then
    hermes_bin=$SPARTAN_CAMOFOX_HERMES_BIN
fi
backup_parent="$data_root/.spartan-backups"
backup_dir="$backup_parent/camofox-profile-isolation-original"
latest_file="$backup_parent/camofox-profile-isolation.latest"
manifest="$backup_dir/profiles"

config_path() {
    if [ "$1" = default ]; then
        printf '%s\n' "$data_root/config.yaml"
    else
        printf '%s/profiles/%s/config.yaml\n' "$data_root" "$1"
    fi
}

profile_home() {
    if [ "$1" = default ]; then
        printf '%s\n' "$data_root"
    else
        printf '%s/profiles/%s\n' "$data_root" "$1"
    fi
}

valid_profile_name() {
    case "$1" in
        ""|"."|".."|*[!A-Za-z0-9._-]*) return 1 ;;
        *) return 0 ;;
    esac
}

preflight_profile_paths() {
    python3 - "$1" "$data_root" "$backup_parent" "$backup_dir" "$latest_file" "$manifest" <<'PYPATHS' >/dev/null 2>&1
import pathlib
import stat
import sys

profiles = sys.argv[1].split()
root = pathlib.Path(sys.argv[2])
backup_parent = pathlib.Path(sys.argv[3])
backup_dir = pathlib.Path(sys.argv[4])
latest_file = pathlib.Path(sys.argv[5])
manifest = pathlib.Path(sys.argv[6])

if not root.is_absolute():
    raise SystemExit(1)

def lstat_or_none(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None

def require_directory(path):
    current = lstat_or_none(path)
    if current is None or stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
        raise SystemExit(1)

def require_regular(path):
    current = lstat_or_none(path)
    if current is None or stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
        raise SystemExit(1)

require_directory(root)
root_real = root.resolve(strict=True)

def require_contained(path):
    try:
        path.resolve(strict=True).relative_to(root_real)
    except (FileNotFoundError, RuntimeError, ValueError):
        raise SystemExit(1)

for profile in profiles:
    home = root if profile == "default" else root / "profiles" / profile
    if profile != "default":
        require_directory(root / "profiles")
        require_contained(root / "profiles")
        require_directory(home)
        require_contained(home)
    config = home / "config.yaml"
    require_regular(config)
    require_contained(config)

for directory in (backup_parent, backup_dir):
    if lstat_or_none(directory) is not None:
        require_directory(directory)
        require_contained(directory)
for regular in (latest_file, manifest):
    if lstat_or_none(regular) is not None:
        require_regular(regular)
        require_contained(regular)
PYPATHS
}

append_profile() {
    candidate=$1
    valid_profile_name "$candidate" || return 1
    case " $profiles " in
        *" $candidate "*) return 0 ;;
    esac
    path=$(config_path "$candidate")
    if [ ! -f "$path" ] || [ -L "$path" ]; then
        [ "$candidate" != default ] && [ "$discovering" = true ] && return 0
        return 1
    fi
    profiles="$profiles $candidate"
    profile_count=$((profile_count + 1))
}

discovering=false
profiles=
profile_count=0
if [ "$#" -gt 0 ]; then
    for requested in "$@"; do
        append_profile "$requested" || exit 1
    done
else
    discovering=true
    append_profile default || exit 1
    old_ifs=$IFS
    IFS=', '
    for requested in $HERMES_AUTOSTART_PROFILES; do
        append_profile "$requested" || exit 1
    done
    IFS=$old_ifs
fi
[ "$profile_count" -gt 0 ] || exit 1
profiles=${profiles# }
preflight_profile_paths "$profiles" || exit 1

preflight_backup() {
    [ -d "$backup_dir" ] && [ ! -L "$backup_dir" ] || return 1
    [ -f "$manifest" ] && [ ! -L "$manifest" ] || return 1
    seen=
    count=0
    for saved in $(sed -n '/./p' "$manifest"); do
        valid_profile_name "$saved" || return 1
        case " $seen " in *" $saved "*) return 1 ;; esac
        saved_config="$backup_dir/$saved.yaml"
        destination=$(config_path "$saved")
        [ -f "$saved_config" ] && [ ! -L "$saved_config" ] || return 1
        [ -f "$destination" ] && [ ! -L "$destination" ] || return 1
        seen="$seen $saved"
        count=$((count + 1))
    done
    [ "$count" -gt 0 ] || return 1
    preflight_profile_paths "$seen"
}

restore_from_backup() {
    preflight_backup || return 1
    restore_files=
    for saved in $(sed -n '/./p' "$manifest"); do
        staged="$backup_dir/.restore-$saved-$$"
        rm -f "$staged"
        cp -p "$backup_dir/$saved.yaml" "$staged" || {
            for prior in $restore_files; do rm -f "$prior"; done
            return 1
        }
        chmod 600 "$staged"
        restore_files="$restore_files $staged"
    done
    index=1
    for saved in $(sed -n '/./p' "$manifest"); do
        staged=$(printf '%s\n' "$restore_files" | awk -v n="$index" '{print $n}')
        mv -f "$staged" "$(config_path "$saved")" || return 1
        index=$((index + 1))
    done
}

if [ "$action" = rollback ]; then
    [ -f "$latest_file" ] && [ ! -L "$latest_file" ] || exit 1
    [ "$(sed -n '1p' "$latest_file")" = "$backup_dir" ] || exit 1
    restore_from_backup || exit 1
    printf 'rollback=true profiles=%s\n' "$(sed -n '/./p' "$manifest" | wc -l | tr -d ' ')"
    exit 0
fi
[ "$action" = apply ] || exit 2

umask 077
if [ -e "$backup_parent" ]; then
    [ -d "$backup_parent" ] && [ ! -L "$backup_parent" ] || exit 1
else
    mkdir -p "$backup_parent"
fi
chmod 700 "$backup_parent"

if [ ! -e "$backup_dir" ]; then
    staging="$backup_parent/.camofox-profile-isolation-original-$$"
    mkdir "$staging"
    chmod 700 "$staging"
    for profile in $profiles; do
        cp -p "$(config_path "$profile")" "$staging/$profile.yaml"
        chmod 600 "$staging/$profile.yaml"
    done
    for profile in $profiles; do printf '%s\n' "$profile"; done > "$staging/profiles"
    chmod 600 "$staging/profiles"
    mv "$staging" "$backup_dir"
else
    preflight_backup || exit 1
    additions=
    for profile in $profiles; do
        if ! grep -Fxq "$profile" "$manifest"; then
            cp -p "$(config_path "$profile")" "$backup_dir/.$profile.yaml.new-$$"
            chmod 600 "$backup_dir/.$profile.yaml.new-$$"
            additions="$additions $profile"
        fi
    done
    if [ -n "$additions" ]; then
        cp -p "$manifest" "$backup_dir/.profiles.new-$$"
        for profile in $additions; do
            mv "$backup_dir/.$profile.yaml.new-$$" "$backup_dir/$profile.yaml"
            printf '%s\n' "$profile" >> "$backup_dir/.profiles.new-$$"
        done
        chmod 600 "$backup_dir/.profiles.new-$$"
        mv "$backup_dir/.profiles.new-$$" "$manifest"
    fi
fi
preflight_backup || exit 1

if [ -e "$latest_file" ]; then
    [ -f "$latest_file" ] && [ ! -L "$latest_file" ] || exit 1
    [ "$(sed -n '1p' "$latest_file")" = "$backup_dir" ] || exit 1
else
    printf '%s\n' "$backup_dir" > "$latest_file.new-$$"
    chmod 600 "$latest_file.new-$$"
    mv "$latest_file.new-$$" "$latest_file"
fi

rollback_on_error() {
    restore_from_backup >/dev/null 2>&1 || true
}
on_signal() {
    rollback_on_error
    exit 1
}
trap on_signal HUP INT TERM

for profile in $profiles; do
    if [ "$profile" = default ]; then
        profile_args=
    else
        profile_args="-p $profile"
    fi
    # profile names are restricted above, so intentional word splitting is safe.
    # shellcheck disable=SC2086
    "$hermes_bin" $profile_args config set browser.camofox.managed_persistence true >/dev/null 2>&1 \
        && "$hermes_bin" $profile_args config set browser.camofox.adopt_existing_tab false >/dev/null 2>&1 \
        && "$hermes_bin" $profile_args config set browser.camofox.user_id "" >/dev/null 2>&1 \
        && "$hermes_bin" $profile_args config set browser.camofox.session_key "" >/dev/null 2>&1 \
        || { rollback_on_error; exit 1; }
done

python3 - "$profiles" "$data_root" <<'PYVERIFY' >/dev/null 2>&1 || { rollback_on_error; exit 1; }
import os
import pathlib
import sys
import yaml

from tools.browser_camofox_state import get_camofox_identity

profiles = sys.argv[1].split()
root = pathlib.Path(sys.argv[2])
user_ids = set()
session_keys = set()
for profile in profiles:
    home = root if profile == "default" else root / "profiles" / profile
    path = home / "config.yaml"
    data = yaml.safe_load(path.read_text()) or {}
    camofox = ((data.get("browser") or {}).get("camofox") or {})
    if camofox.get("managed_persistence") is not True:
        raise SystemExit(1)
    if camofox.get("adopt_existing_tab") is not False:
        raise SystemExit(1)
    if str(camofox.get("user_id") or "").strip():
        raise SystemExit(1)
    if str(camofox.get("session_key") or "").strip():
        raise SystemExit(1)
    os.environ["HERMES_HOME"] = str(home)
    identity = get_camofox_identity("spartan-profile-isolation-check")
    user_id = identity.get("user_id")
    session_key = identity.get("session_key")
    if not isinstance(user_id, str) or not user_id:
        raise SystemExit(1)
    if not isinstance(session_key, str) or not session_key:
        raise SystemExit(1)
    if user_id in user_ids or session_key in session_keys:
        raise SystemExit(1)
    user_ids.add(user_id)
    session_keys.add(session_key)
PYVERIFY

trap - HUP INT TERM
for profile in $profiles; do printf 'profile=%s parse_ok=true\n' "$profile"; done
printf 'distinct=true profiles=%s\n' "$profile_count"
