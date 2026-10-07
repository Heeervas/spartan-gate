"""Bind raw API delegation claims, persistence, and settlement to their owner."""
from pathlib import Path

TARGET = Path('/opt/hermes/gateway/run_notifications.py')
MARKER = '# Spartan Gate: prove raw API completion ownership before any ledger access.'
HELPER = '''    # Spartan Gate: prove raw API completion ownership before any ledger access.
    def _raw_completion_owner_home(self, raw_sid):
        from gateway.run import _multiplex_profile_homes
        from gateway.wake import session_owned_by_profile

        homes = dict(_multiplex_profile_homes(self.config))
        # A cached source inferred from this client-chosen ID is not an ownership hint.
        owners = [name for name in homes
                  if session_owned_by_profile(self.config, name, raw_sid)]
        if len(owners) != 1:
            raise LookupError("raw completion session has no unique served owner")
        return homes[owners[0]]

    @contextlib.asynccontextmanager
    async def _completion_event_scope(self, evt):
        from gateway.run import _async_profile_runtime_scope
        from hermes_constants import get_hermes_home_override

        raw_sid = _raw_process_event_session_id(evt)
        if (evt.get("type") == "async_delegation" and raw_sid
                and getattr(self.config, "multiplex_profiles", False)):
            home = await asyncio.to_thread(self._raw_completion_owner_home, raw_sid)
            scope = (contextlib.nullcontext() if get_hermes_home_override() == str(home)
                     else _async_profile_runtime_scope(home))
        else:
            scope = self._routed_completion_event_scope(evt)
        async with scope:
            yield

'''
ANCHOR = '    def _completion_event_scope(self, evt: dict):'
ASYNC_ANCHOR = '''        if evt.get("type") == "async_delegation":
            info = "Async delegation completion — persisting delivery row for api_server session %s (no wake turn)"'''
ASYNC_REPLACEMENT = '''        if evt.get("type") == "async_delegation":
            if raw_sid != _raw_process_event_session_id(evt):
                return False
            scope = self._completion_event_scope(evt)
            info = "Async delegation completion — persisting delivery row for api_server session %s (no wake turn)"'''
SOURCE_ANCHOR = '        source = await asyncio.to_thread(self._build_process_event_source, evt)'
SOURCE_REPLACEMENT = '''        # Raw API completions must stay on the durable API delivery path, even with a cached origin.
        source = (None if evt.get("type") == "async_delegation" and _raw_process_event_session_id(evt)
                  else await asyncio.to_thread(self._build_process_event_source, evt))'''


def patch_text(source):
    if MARKER in source:
        if source.count(HELPER) != 1 or source.count(ASYNC_REPLACEMENT) != 1 or source.count(SOURCE_REPLACEMENT) != 2:
            raise ValueError('incomplete raw completion scope repair')
        return source
    if source.count(ANCHOR) != 1 or source.count(ASYNC_ANCHOR) != 1 or source.count(SOURCE_ANCHOR) != 2:
        raise ValueError('raw completion scope anchors changed')
    result = source.replace(ANCHOR, HELPER + ANCHOR.replace('_completion_event_scope', '_routed_completion_event_scope'), 1)
    result = result.replace(ASYNC_ANCHOR, ASYNC_REPLACEMENT, 1)
    result = result.replace(SOURCE_ANCHOR, SOURCE_REPLACEMENT)
    compile(result, str(TARGET), 'exec')
    return result


def main():
    source = TARGET.read_text()
    result = patch_text(source)
    if result != source:
        TARGET.write_text(result)
    print('OK: raw API delegation delivery retains its owning profile')


if __name__ == '__main__':
    main()
