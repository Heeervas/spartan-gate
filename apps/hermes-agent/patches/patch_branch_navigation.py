"""Add native terminal/Discord branch navigation against the pinned Hermes source.

All targets/anchors/compilation are preflighted before the first write. Helpers are
installed separately by Docker COPY; this script does not copy runtime/profile data.
"""
from pathlib import Path
import sys

ROOT = Path('/opt/hermes')

CLI_METHODS = '''    def _handle_start_fresh_command(self, cmd):
        return self._handle_branch_command(cmd)

    def _handle_return_command(self, cmd):
        from spartan_branch_navigation import cli_return
        return cli_return(self)

    def _handle_cancel_command(self, cmd):
        from spartan_branch_navigation import cli_return
        return cli_return(self, cancel=True)

'''
GATEWAY_METHODS = '''    async def _handle_start_fresh_command(self, event):
        if event.source.platform != Platform.DISCORD:
            return "Branch navigation is supported in terminal CLI and Discord only."
        return await self._handle_branch_command(event)

    async def _handle_return_command(self, event):
        from spartan_branch_gateway import gateway_return
        return await gateway_return(self, event)

    async def _handle_cancel_command(self, event):
        from spartan_branch_gateway import gateway_return
        return await gateway_return(self, event, cancel=True)

'''

# Exact upstream seams. No generated framework, second session manager or delivery ledger.
EDITS = {
    'hermes_cli/commands.py': [
        ('"Session", aliases=("fork",), args_hint="[--here] [name]"),',
         '"Session", aliases=("fork", "start-branch"), args_hint="[--here] [name]"),\n'
         '    CommandDef("start-fresh", "Open an empty navigation child (Discord requires a new thread)", "Session", args_hint="[name]"),\n'
         '    CommandDef("return", "Return to navigation parent (CLI or separate Discord thread)", "Session"),\n'
         '    CommandDef("cancel", "Cancel navigation without importing (not filesystem rollback)", "Session"),'),
    ],
    'hermes_cli/cli_commands_mixin.py': [
        ('    def _handle_branch_command(self, cmd_original: str) -> None:',
         CLI_METHODS + '    def _handle_branch_command(self, cmd_original: str) -> None:'),
        ('        if not self.conversation_history:\n            return _cp(f"  {_gt(\'branch.no_conversation\')}")',
         '        fresh = cmd_original.split()[0].lower() == "/start-fresh"\n'
         '        if not self.conversation_history and not fresh:\n            return _cp(f"  {_gt(\'branch.no_conversation\')}")'),
        ('                for msg in self.conversation_history], chunk_rows=500)',
         '                for msg in ([] if fresh else self.conversation_history)], chunk_rows=500)\n'
         '        from spartan_branch_navigation import record_navigation\n'
         '        record_navigation(self._session_db, new_session_id, parent_session_id)\n'
         '        if fresh:\n            self.conversation_history = []'),
    ],
    'gateway/slash_commands_session.py': [
        ('    async def _handle_branch_command(self, event: MessageEvent) -> str:',
         GATEWAY_METHODS + '    async def _handle_branch_command(self, event: MessageEvent) -> str:'),
        ('        if not history:\n            return t("gateway.branch.no_conversation")',
         '        fresh = event.get_command() == "start-fresh"\n'
         '        if not history and not fresh:\n            return t("gateway.branch.no_conversation")'),
        ('        in_place = dest_source is None\n        if in_place:',
         '        in_place = dest_source is None\n'
         '        if fresh and source.platform == Platform.DISCORD and in_place:\n'
         '            return "Navigation needs a new Discord thread; use a server channel with thread permissions (not --here)."\n'
         '        if in_place:'),
        ('                new_session_id, [_branch_row(msg) for msg in history], chunk_rows=500)',
         '                new_session_id, [_branch_row(msg) for msg in ([] if fresh else history)], chunk_rows=500)\n'
         '        if source.platform == Platform.DISCORD and not in_place:\n'
         '            from spartan_branch_navigation import record_navigation\n'
         '            from spartan_branch_gateway import database\n'
         '            await asyncio.to_thread(record_navigation, database(self), new_session_id,\n'
         '                                    parent_session_id, source.to_dict())\n'
         '        if fresh:\n            history = []'),
    ],
    'gateway/run_busy.py': [
        ('        "notification_category",\n    )',
         '        "notification_category", "spartan_branch_return",\n    )'),
        ('"title", "resume", "sessions", "branch", "rollback", "diff", "goal",',
         '"title", "resume", "sessions", "branch", "start-fresh", "return", "cancel", "rollback", "diff", "goal",'),
    ],
    'gateway/run_turn.py': [
        ('        event_metadata = getattr(event, "metadata", None) or {}\n        expected_session_key',
         '        event_metadata = getattr(event, "metadata", None) or {}\n'
         '        if event.internal and event_metadata.get("spartan_branch_return"):\n'
         '            from spartan_branch_gateway import resolve_return_event\n'
         '            return await resolve_return_event(self, event)\n'
         '        expected_session_key'),
        ('                    "gateway_input_owner": prepared.persistence_owner,',
         '                    **({"spartan_branch_return": event.metadata["spartan_branch_return"]}\n'
         '                       if event.internal and event.metadata.get("spartan_branch_return") else {}),\n'
         '                    "gateway_input_owner": prepared.persistence_owner,'),
        ('                    **reply_expected_metadata(next_reply_expected), **diagnostic_metadata(pending_event)} or None,',
         '                    **({"spartan_branch_return": pending_event.metadata["spartan_branch_return"]}\n'
         '                       if pending_event is not None and pending_event.internal\n'
         '                       and pending_event.metadata.get("spartan_branch_return") else {}),\n'
         '                    **reply_expected_metadata(next_reply_expected), **diagnostic_metadata(pending_event)} or None,'),
        ('        with self._profile_scope_for_source(source):\n            return await self._run_agent_inner(message, context_prompt, history, source, session_id, **turn_kwargs)',
         '        with self._profile_scope_for_source(source):\n'
         '            receipt = (turn_kwargs.get("persist_user_display_metadata") or {}).get("spartan_branch_return")\n'
         '            if receipt:\n'
         '                from spartan_branch_gateway import run_return_turn\n'
         '                return await run_return_turn(self, dict(message=message, context_prompt=context_prompt,\n'
         '                    history=history, source=source, session_id=session_id, **turn_kwargs), receipt)\n'
         '            return await self._run_agent_inner(message, context_prompt, history, source, session_id, **turn_kwargs)'),
        ('            result = turn_ctx.result_holder[0]\n            adapter = self._delivery_adapter_for(source)',
         '            result = turn_ctx.result_holder[0]\n'
         '            receipt = (turn_ctx.persist_user_display_metadata or {}).get("spartan_branch_return")\n'
         '            if receipt:\n'
         '                from spartan_branch_gateway import finish_return\n'
         '                await finish_return(self, receipt, result)\n'
         '            adapter = self._delivery_adapter_for(source)'),
    ],
    'gateway/run_turn_runner.py': [
        ('            with notification_turn(agent, muted=ctx.mute_notification_reply, session_id=ctx.session_id or ""):',
         '            from spartan_branch_navigation import acknowledgment_turn\n'
         '            ack_only = bool((ctx.persist_user_display_metadata or {}).get("spartan_branch_return"))\n'
         '            with acknowledgment_turn(agent, enabled=ack_only), notification_turn(agent, muted=ctx.mute_notification_reply, session_id=ctx.session_id or ""):'),
    ],
    'agent/conversation_loop.py': [
        ('    from tools.mcp_tool_agent import agent_tool_names, persist_agent_tool_names, restore_agent_tool_prefix\n    built_for_this_surface',
         '    from spartan_branch_navigation import acknowledgment_only, clear_ack_tools\n'
         '    if acknowledgment_only():\n        clear_ack_tools(agent)\n        return []\n'
         '    from tools.mcp_tool_agent import agent_tool_names, persist_agent_tool_names, restore_agent_tool_prefix\n    built_for_this_surface'),
    ],
    'agent/turn_context.py': [
        ('    try:\n        # An authorization that committed after its connection card closed:',
         '    from spartan_branch_navigation import acknowledgment_only, clear_ack_tools\n'
         '    if acknowledgment_only():\n        clear_ack_tools(agent)\n        return\n'
         '    try:\n        # An authorization that committed after its connection card closed:'),
    ],
    'agent/turn_tool_round.py': [
        ('    from agent.conversation_loop import _invalid_tool_name_error_content',
         '    from spartan_branch_navigation import acknowledgment_only\n'
         '    if acknowledgment_only():\n'
         '        raise RuntimeError("Branch acknowledgment cannot execute tools")\n'
         '    from agent.conversation_loop import _invalid_tool_name_error_content'),
    ],
    'tools/mcp_tool_agent.py': [
        ('    db = getattr(agent, "_session_db", None)\n    session_id = getattr(agent, "session_id", None)',
         '    from spartan_branch_navigation import acknowledgment_only\n'
         '    if acknowledgment_only():\n        return\n'
         '    db = getattr(agent, "_session_db", None)\n    session_id = getattr(agent, "session_id", None)'),
    ],
}


def patch_text(source, edits, name):
    for old, new in edits:
        if source.count(new) == 1:
            continue
        if source.count(old) != 1:
            raise ValueError(f'{name}: branch navigation anchor changed or partially patched')
        source = source.replace(old, new, 1)
    compile(source, name, 'exec')
    return source


def apply(root):
    prepared = []
    for name, edits in EDITS.items():
        path = root / name
        original = path.read_text()
        prepared.append((path, original, patch_text(original, edits, name)))
    for path, original, result in prepared:
        if result != original:
            path.write_text(result)


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT
    apply(root)
    print('OK: native terminal/Discord branch navigation installed')


if __name__ == '__main__':
    main()
