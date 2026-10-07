"""Warn about lost origin threads only for deliveries resolved from that origin."""
from pathlib import Path

TARGET = Path('/opt/hermes/cron/scheduler_delivery.py')
ANCHOR = '    if origin_thread and not thread_id:'
REPLACEMENT = '''    # Spartan Gate: explicit destinations and native stale-thread fallback are intentional.
    if (origin_thread and not thread_id and target.get("_resolved_from") == "origin"
            and _origin_delivery_thread(origin)
            and not (str(platform_name).lower() == "discord"
                     and str(chat_id) == str(origin_thread))):'''


def patch_text(source):
    if REPLACEMENT in source:
        return source
    if source.count(ANCHOR) != 1:
        raise ValueError('cron origin-thread diagnostic anchor changed')
    result = source.replace(ANCHOR, REPLACEMENT, 1)
    compile(result, str(TARGET), 'exec')
    return result


def main():
    source = TARGET.read_text()
    result = patch_text(source)
    if result != source:
        TARGET.write_text(result)
    print('OK: cron thread diagnostic preserves intentional delivery destinations')


if __name__ == '__main__':
    main()
