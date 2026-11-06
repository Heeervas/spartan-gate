#!/usr/bin/env python3
"""Adapt native unprivileged s6 profile gateways to Gate's numeric data owner.

Upstream owns the separate dynamic scanner, atomic slot publication and runtime
registration. Do not restore the retired root-scandir workaround.
"""
from pathlib import Path

TARGET = Path('/opt/hermes/hermes_cli/service_manager.py')
MARKER = '# Spartan Gate: numeric owner for native profile supervision'

OWNER_OLD = '''    import pwd

    try:
        return pwd.getpwnam("hermes")
'''
OWNER_NEW = '''    # Spartan Gate: numeric owner for native profile supervision
    uid = os.environ.get("SPARTAN_HERMES_RUN_UID")
    gid = os.environ.get("SPARTAN_HERMES_RUN_GID")
    if uid is not None or gid is not None:
        if not uid or not gid or not uid.isdecimal() or not gid.isdecimal():
            raise ValueError("Hermes runtime UID/GID must be a numeric pair")
        from types import SimpleNamespace
        return SimpleNamespace(pw_uid=int(uid), pw_gid=int(gid))
    import pwd

    try:
        return pwd.getpwnam("hermes")
'''
CONTROL_OLD = '''        if _root_hermes_account() is not None:
            return _s6_run("s6-setuidgid", "hermes", f"{_S6_BIN_DIR}/{cmd}", *args,
                           timeout=timeout, check=check)
'''
CONTROL_NEW = '''        account = _root_hermes_account()
        if account is not None:
            return _s6_run("s6-applyuidgid", "-u", str(account.pw_uid), "-g", str(account.pw_gid),
                           f"{_S6_BIN_DIR}/{cmd}", *args, timeout=timeout, check=check)
'''
ENV_OLD = '            "cd /opt/data",\n'
ENV_NEW = ENV_OLD + '''            "for key in CAMOFOX_URL CAMOFOX_API_KEY CAMOFOX_ACCESS_KEY CAMOFOX_USER_ID CAMOFOX_SESSION_KEY HERMES_MEET_CAMOFOX_SESSION_KEY CAMOFOX_ADOPT_EXISTING_TAB; do",
            '    file="/run/s6/container_environment/$key"',
            '    if [ -f "$file" ]; then export "$key=$(cat "$file")"; fi',
            "done",
            "unset key file",
'''
DROP_OLD = '        lines.append(f"exec s6-setuidgid hermes {gateway_cmd}")\n'
DROP_NEW = '''        lines.append('exec /command/s6-applyuidgid -u "${SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}" '
                     f'-g "${{SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}}" {gateway_cmd}')
'''
LOG_OLD = "s6-setuidgid hermes "
LOG_NEW = '/command/s6-applyuidgid -u "${{SPARTAN_HERMES_RUN_UID:-$(id -u hermes)}}" -g "${{SPARTAN_HERMES_RUN_GID:-$(id -g hermes)}}" '


def patch_source(source: str) -> str:
    """Preflight the complete pinned shape, including already-patched input."""
    replacements = ((OWNER_OLD, OWNER_NEW), (CONTROL_OLD, CONTROL_NEW),
                    (ENV_OLD, ENV_NEW), (DROP_OLD, DROP_NEW))
    updated = source
    patched = MARKER in source
    for old, new in replacements:
        expected = new if patched else old
        if updated.count(expected) != 1:
            raise ValueError('native unprivileged service-manager anchor changed')
        if not patched:
            updated = updated.replace(old, new, 1)
    # Only the three f-string log commands remain after control/run adaptation.
    if not patched:
        if updated.count(LOG_OLD) != 3:
            raise ValueError('native gateway logger identity anchors changed')
        updated = updated.replace(LOG_OLD, LOG_NEW)
    elif updated.count(LOG_NEW) != 3 or LOG_OLD in updated:
        raise ValueError('incomplete native gateway logger identity patch')
    compile(updated, str(TARGET), 'exec')
    return updated


def main() -> None:
    original = TARGET.read_text(encoding='utf-8')
    updated = patch_source(original)
    if updated != original:
        TARGET.write_text(updated, encoding='utf-8')
    print('Verified native unprivileged profile gateways and numeric runtime identity')


if __name__ == '__main__':
    main()
