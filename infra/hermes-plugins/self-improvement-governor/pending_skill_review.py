"""Native cron wake precheck: profile-local queue counts, never proposal content."""
import json
import os
import subprocess
import sys
from stat import S_ISDIR

from hermes_cli.profiles import _iter_named_profile_dirs, get_profile_dir


_QUEUE_COUNTS = """import json
from tools import write_approval as wa
from agent.skill_utils import _skills_cfg
records = wa.list_pending('skills')
config = _skills_cfg() or {}
print(json.dumps({'pending': len(records), 'files': wa.pending_count('skills'),
                  'approval_enabled': wa._normalize_enabled(config.get('write_approval'))}))
"""


def main():
    queues = []
    try:
        registry = get_profile_dir('default') / 'profiles'
        try:
            mode = registry.stat().st_mode
        except FileNotFoundError:
            names = ['default']
        else:
            if not S_ISDIR(mode):
                raise OSError('native profile registry is not a directory')
            names = ['default', *(p.name for p in _iter_named_profile_dirs())]
    except OSError as error:
        print(json.dumps({'coverage_error': str(error), 'wakeAgent': True}))
        return
    for name in names:
        result = subprocess.check_output(
            [sys.executable, '-B', '-c', _QUEUE_COUNTS], text=True,
            env={**os.environ, 'HERMES_HOME': str(get_profile_dir(name)),
                 'PYTHONDONTWRITEBYTECODE': '1'})
        counts = json.loads(result)
        if counts['files'] or not counts['approval_enabled']:
            queues.append({'profile': name, **counts})
    print(json.dumps({'profile_queues': queues, 'wakeAgent': bool(queues)}))


if __name__ == '__main__':
    main()
