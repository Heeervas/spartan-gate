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
         '    CommandDef("start-fresh", "Open an empty navigation child (Discord requires a new thread)", "Session", args_hint="[name]", desktop="terminal"),\n'
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
    'gateway/run_turn_prepare.py': [
        ('        event_metadata = getattr(event, "metadata", None) or {}\n        expected_session_key',
         '        event_metadata = getattr(event, "metadata", None) or {}\n'
         '        if event.internal and event_metadata.get("spartan_branch_return"):\n'
         '            from spartan_branch_gateway import resolve_return_event\n'
         '            return await resolve_return_event(self, event)\n'
         '        expected_session_key'),
    ],
    'gateway/run_turn.py': [
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
         '            from spartan_branch_navigation import acknowledgment_turn, acknowledgment_only\n'
         '            ack_only = acknowledgment_only() or bool((ctx.persist_user_display_metadata or {}).get("spartan_branch_return"))\n'
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


# Desktop's authenticated authority remains separate from legacy sidecar dispatch.
EDITS.update({
    'tui_gateway/server.py': [
        ('    "wake.status", "session.active_list", "session.branch", "session.compress", "session.list",',
         '    "session.branch_return", "session.branch_cancel",  # bounded native long-handler pool\n'
         '    "wake.status", "session.active_list", "session.branch", "session.compress", "session.list",'),
    ],
    'hermes_state_mutation_branch.py': [
        ("    conn.execute('UPDATE sessions SET message_count=?,tool_call_count=? WHERE id=?', (len(ids), tools, child))",
         "    conn.execute('UPDATE sessions SET message_count=?,tool_call_count=? WHERE id=?', (len(ids), tools, child))\n"
         "    from spartan_branch_navigation import record_committed_branch\n"
         "    record_committed_branch(conn, child, session_id)"),
    ],
    'gateway/session_controls.py': [
        ("        return handlers\n", "        from spartan_branch_desktop import authority_handlers\n"
         "        handlers.update(authority_handlers(self))\n        return handlers\n"),
    ],
    'gateway/session_finite.py': [
        ("    with finite_turn_scope(row['payload'].get('finite', False), row['payload'].get('unattended') is True), \\\n",
         "    from spartan_branch_desktop import admission_ack_scope\n"
         "    with admission_ack_scope(authority, ref, row), \\\n"
         "            finite_turn_scope(row['payload'].get('finite', False), row['payload'].get('unattended') is True), \\\n"),
    ],
    'gateway/session_managed_worker.py': [
        ("    request = dict(json.loads(policy.request_json), turn_v1={",
         "    from spartan_branch_navigation import acknowledgment_only\n"
         "    request = dict(json.loads(policy.request_json), turn_v1={\n"
         "        'spartan_branch_ack': acknowledgment_only(),"),
        ("if k not in ('surface_v1', 'display_v1')", "if k not in ('surface_v1', 'display_v1', 'spartan_branch_ack')"),
        ("    if (flags is None or set(flags) != {'finite', 'unattended', 'yolo'}",
         "    if (flags is None or type(turn.get('spartan_branch_ack', False)) is not bool\n"
         "            or set(flags) != {'finite', 'unattended', 'yolo'}"),
        ("    with finite_turn_scope(turn['finite'], turn['unattended']), surface_turn_scope(surface), \\\n",
         "    from spartan_branch_navigation import acknowledgment_scope\n"
         "    with acknowledgment_scope(turn.get('spartan_branch_ack', False)), \\\n"
         "            finite_turn_scope(turn['finite'], turn['unattended']), surface_turn_scope(surface), \\\n"),
    ],
    'gateway/session_kanban.py': [
        ('    with worker_turn_scope(frame):',
         '    from spartan_branch_navigation import acknowledgment_turn, acknowledgment_only\n'
         '    with worker_turn_scope(frame), acknowledgment_turn(agent, enabled=acknowledgment_only()):'),
    ],
    'tui_gateway/methods_session.py': [
        ('        sync_flushed_message_markers(history, rows)\n',
         '        sync_flushed_message_markers(history, rows)\n'
         '        from spartan_branch_navigation import record_navigation\n'
         '        record_navigation(db, new_key, parent_key)\n'),
        ('def register(server) -> None:\n',
         'def register(server) -> None:\n'
         '    from spartan_branch_desktop import register_legacy\n'
         '    register_legacy(server)\n'),
    ],
    'tui_gateway/methods_prompt.py': [
        ('        if has_truncation:\n            # A rewind/edit/restore/regenerate',
         '        if has_truncation or (session.get("_spartan_branch_ack") or {}).get("text") == text:\n'
         '            # A branch handoff never interrupts, steers or merges into another turn.\n'
         '            # A rewind/edit/restore/regenerate'),
    ],
    'tui_gateway/session_workdir.py': [
        ('            session["_branch_seed_persisted"] = True\n',
         '            if session.get("parent_session_id"):\n'
         '                from spartan_branch_navigation import record_navigation\n'
         '                record_navigation(db, key, session["parent_session_id"])\n'
         '            session["_branch_seed_persisted"] = True\n'),
        ('            _workdir_reraise_disk_full(exc, "branch seed persist failed")\n',
         '            _workdir_reraise_disk_full(exc, "branch seed persist failed")\n'
         '            if session.get("parent_session_id"):\n                raise\n'),
    ],
    'tui_gateway/contracts/sessions.py': [
        ('# ── shared live-session snapshot',
         'class BranchNavigationResult(Result):\n'
         '    status: str\n    parent_session_id: str\n\n\n'
         'method("session.branch_return", params=SessionParams, result=BranchNavigationResult)\n'
         'method("session.branch_cancel", params=SessionParams, result=BranchNavigationResult)\n\n\n'
         '# ── shared live-session snapshot'),
    ],
    'tui_gateway/prompt_turn.py': [
        ('        with notification_turn(agent, muted=event_presentation_muted("message.delta", sid), session_id=sid):',
         '        from spartan_branch_desktop import legacy_ack_turn\n'
         '        with legacy_ack_turn(agent, session, text), notification_turn(agent, muted=event_presentation_muted("message.delta", sid), session_id=sid):'),
        ('            st.result = agent.run_conversation(run_message, **st.run_kwargs)\n',
         '            st.result = agent.run_conversation(run_message, **st.run_kwargs)\n'
         '            from spartan_branch_desktop import legacy_ack_result\n'
         '            legacy_ack_result(session, text, st.result)\n'),
    ],
})


FRONTEND_EDITS = {
    'apps/desktop/src/lib/desktop-slash-commands.ts': [
        ("  | 'branch'\n", "  | 'branch'\n  | 'branch-return'\n  | 'branch-cancel'\n"),
        ("    aliases: ['/fork'],", "    aliases: ['/fork', '/start-branch'],"),
        ("    surface: action('branch')\n  },", "    surface: action('branch')\n  },\n"
         "  { name: '/return', description: 'Summarize branch work into its parent and return', surface: action('branch-return') },\n"
         "  { name: '/cancel', description: 'Return without importing (history and files retained)', surface: action('branch-cancel') },"),
    ],
    'apps/desktop/src/app/session/hooks/use-prompt-actions/slash.ts': [
        ("  setSessions,\n", "  setSessions,\n  setSessionOwnerHint,\n"),
        ("import { remoteBrowserManageRefusal }", "import { resolveSessionOwner } from '../use-session-actions/utils'\n\nimport { remoteBrowserManageRefusal }"),
        ("        branch: async () => {\n          await branchCurrentSession()\n        },",
         "        branch: async () => {\n          await branchCurrentSession()\n        },\n"
         "        'branch-return': ctx => navigateBranch(ctx, false),\n"
         "        'branch-cancel': ctx => navigateBranch(ctx, true),"),
        ("      // `exec` commands (and unknown skill / quick commands the backend owns)",
         "      const navigateBranch = async (ctx: SlashActionCtx, cancel: boolean) => {\n"
         "        if (!initialStoredId || ctx.arg.trim()) {\n"
         "          notify({ kind: 'error', message: 'Open a branch and use /return or /cancel without arguments.' })\n"
         "          return\n        }\n"
         "        const out = await withSlashOutput(ctx)\n        if (!out) return\n"
         "        try {\n"
         "          const owner = destination.owner ?? await resolveSessionOwner(initialStoredId)\n"
         "          const bound = captureSubmissionDestination(initialStoredId, requestGateway, { owner })\n"
         "          const result = await bound.requestGateway<{ status: string; parent_session_id: string }>(\n"
         "            cancel ? 'session.branch_cancel' : 'session.branch_return',\n"
         "            { session_id: out.sessionId }, SESSION_COMPRESS_TIMEOUT_MS)\n"
         "          if (result.status !== (cancel ? 'cancelled' : 'completed') || !result.parent_session_id) {\n"
         "            throw new Error('Branch navigation did not complete; inspect parent history.')\n          }\n"
         "          if (owner && typeof owner === 'object') setSessionOwnerHint(result.parent_session_id, owner)\n"
         "          await refreshSessions()\n"
         "          if (activeSessionIdRef.current === initialRuntimeId && selectedStoredSessionIdRef.current === initialSelectedId) {\n"
         "            await resumeStoredSession(result.parent_session_id)\n          }\n"
         "        } catch (error) {\n"
         "          out.render(error instanceof Error ? error.message : String(error))\n        }\n      }\n\n"
         "      // `exec` commands (and unknown skill / quick commands the backend owns)"),
    ],
}
EDITS.update(FRONTEND_EDITS)


def patch_text(source, edits, name):
    for old, new in edits:
        if source.count(new) == 1:
            continue
        if source.count(old) != 1:
            raise ValueError(f'{name}: branch navigation anchor changed or partially patched')
        source = source.replace(old, new, 1)
    if name.endswith('.py'):
        compile(source, name, 'exec')
    return source


def apply(root, *, frontend_only=False):
    prepared = []
    for name, edits in (FRONTEND_EDITS if frontend_only else EDITS).items():
        path = root / name
        original = path.read_text()
        prepared.append((path, original, patch_text(original, edits, name)))
    for path, original, result in prepared:
        if result != original:
            path.write_text(result)


def main():
    args = [arg for arg in sys.argv[1:] if arg != '--frontend-only']
    root = Path(args[0]) if args else ROOT
    apply(root, frontend_only='--frontend-only' in sys.argv)
    print('OK: native branch navigation installed (renderer rebuild required)')


if __name__ == '__main__':
    main()
