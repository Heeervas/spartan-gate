"""Discord branch returns via native authorized parent turns and native FIFO queues."""
import asyncio

from spartan_branch_navigation import (
    NAV_KEY, RETURN_KEY, NavigationError, navigation_owner,
    require_open, return_prompt, set_status, successful_ack,
)


def database(runner):
    return getattr(runner._session_db, "_db", runner._session_db)


async def _notice(runner, source, text):
    adapter = runner._delivery_adapter_for(source)
    if adapter is None:
        raise NavigationError("Discord adapter unavailable; inspect the retained branch.")
    result = await adapter.send(source.chat_id, text, metadata={"thread_id": source.thread_id})
    if result is not None and getattr(result, "success", True) is False:
        raise NavigationError("Child notice could not be delivered; inspect the retained branch.")


async def resolve_parent(runner, receipt):
    """Never revive an old parent into a route the user deliberately replaced."""
    from gateway.session import SessionSource
    db = database(runner)
    nav = db.get_session_model_config_value(receipt["owner"], NAV_KEY)
    if not nav or nav.get("return_id") != receipt["id"] or nav["status"] not in {
        "prepared", "queued", "running",
    }:
        raise NavigationError("Return is no longer pending; not importing again.")
    source = SessionSource.from_dict(nav["parent_source"])
    if not runner._is_user_authorized_for_source(source, allow_adapter_delegation=False):
        raise NavigationError("Parent is no longer authorized; nothing imported.")
    key = runner._session_key_for_source(source)
    entry = await runner.async_session_store.lookup_by_session_key(key)
    chain = db.get_compression_chain(nav["parent"])
    if entry is None or entry.session_id not in chain:
        raise NavigationError("Parent conversation was replaced; nothing imported.")
    if runner._get_proxy_url():
        raise NavigationError("Branch acknowledgment is unsupported on remote agent proxies.")
    return source, entry, key


async def finish_return(runner, receipt, result=None, status=None):
    """Called at this acknowledgment's own worker outcome, before FIFO recursion."""
    from gateway.session import SessionSource
    db = database(runner)
    nav = db.get_session_model_config_value(receipt["owner"], NAV_KEY)
    if not nav or nav.get("return_id") != receipt["id"]:
        return
    if nav["status"] in {"completed", "failed", "rejected", "ambiguous"}:
        return
    status = status or ("completed" if successful_ack(result) else "failed")
    set_status(db, receipt["owner"], nav, status)
    child = SessionSource.from_dict(receipt["child_source"])
    text = ("Parent acknowledgment completed (no tools). Branch history retained."
            if status == "completed" else
            f"Parent return {status}; inspect parent/branch history before manual recovery.")
    try:
        await _notice(runner, child, text)
    except Exception:
        # Completion != confirmed Discord delivery. Keep the outcome plus a truthful receipt.
        db.patch_session_model_config(receipt["owner"], {NAV_KEY: {
            **db.get_session_model_config_value(receipt["owner"], NAV_KEY),
            "completion_notice_failed": True,
        }})


async def resolve_return_event(runner, event):
    try:
        return await resolve_parent(runner, event.metadata[RETURN_KEY])
    except NavigationError:
        await finish_return(runner, event.metadata[RETURN_KEY], status="rejected")
        return None


async def run_return_turn(runner, call, receipt):
    """Revalidate at execution, including recursive queued turns that skip ingress."""
    db = database(runner)
    try:
        source, entry, key = await resolve_parent(runner, receipt)
    except NavigationError:
        await finish_return(runner, receipt, status="rejected")
        return {"final_response": "", "messages": call["history"], "completed": False}
    nav = db.get_session_model_config_value(receipt["owner"], NAV_KEY)
    if nav["status"] == "running":
        # An interrupted/duplicate event cannot establish whether the first effect committed.
        await finish_return(runner, receipt, status="ambiguous")
        return {"final_response": "", "messages": call["history"], "completed": False}
    set_status(db, receipt["owner"], nav, "running")
    try:
        # A busy parent may have compressed since admission. Use its current native transcript.
        call.update(source=source, session_id=entry.session_id, session_key=key,
                    history=await runner.async_session_store.load_transcript(entry.session_id))
        return await runner._run_agent_inner(**call)
    except BaseException:
        await finish_return(runner, receipt, status="ambiguous")
        raise


async def gateway_return(runner, event, cancel=False):
    from gateway.config import Platform
    from gateway.platforms.base import MessageEvent, MessageType
    source = event.source
    if source.platform != Platform.DISCORD:
        return "Branch navigation is supported in terminal CLI and Discord only."
    db = database(runner)
    if db is None:
        return "Session database unavailable."
    try:
        entry = await runner.async_session_store.get_or_create_session(source)
        owner, nav = navigation_owner(db, entry.session_id)
        require_open(nav)
        if cancel:
            set_status(db, owner, nav, "cancelled")
            return "Branch cancelled; nothing imported. History and filesystem changes retained."
        # Reserve synchronously before yielding; ordinary repeated /return sees preparing.
        from spartan_branch_navigation import summarize_branch
        import uuid
        nav = set_status(db, owner, nav, "preparing", return_id=uuid.uuid4().hex)
        try:
            summary = await asyncio.to_thread(summarize_branch, db, entry.session_id, nav)
        except BaseException:
            set_status(db, owner, nav, "open", return_id=None)
            raise
        nav = set_status(db, owner, nav, "prepared", summary=summary)
        receipt = {"owner": owner, "id": nav["return_id"], "child_source": source.to_dict()}
        try:
            parent_source, parent_entry, key = await resolve_parent(runner, receipt)
        except NavigationError:
            await finish_return(runner, receipt, status="rejected")
            return "Parent conversation changed or unavailable; nothing imported. History retained."
        adapter = runner._delivery_adapter_for(parent_source)
        if adapter is None:
            set_status(db, owner, nav, "open")
            return "Parent adapter unavailable; nothing imported. Try /return later."
        injected = MessageEvent(
            text=return_prompt(nav), message_type=MessageType.TEXT, source=parent_source,
            internal=True, allow_gateway_control=False, reply_expected=True,
            metadata={RETURN_KEY: receipt, "gateway_session_key": key,
                      "gateway_session_id": parent_entry.session_id})
        try:
            await _notice(runner, source, "Return queued for the parent; waiting for its brief acknowledgment.")
        except BaseException:
            set_status(db, owner, nav, "open", return_id=None)
            raise
        set_status(db, owner, nav, "queued")
        # Native busy ingress preserves metadata and never interrupts for internal events.
        # Schedule rather than await the entire parent turn inside the child's slash handler.
        task = asyncio.create_task(adapter.handle_message(injected))

        async def settled():
            try:
                await task
                current = db.get_session_model_config_value(owner, NAV_KEY)
                if current["status"] == "queued" and not getattr(injected, "_gateway_accepted", False):
                    await finish_return(runner, receipt, status="rejected")
            except BaseException:
                await finish_return(runner, receipt, status="ambiguous")
        # Keep the pending coroutine alive through the dispatch task's callback reference.
        task.add_done_callback(lambda _: asyncio.create_task(settled()))
        return ""
    except Exception as exc:
        return (str(exc) if isinstance(exc, NavigationError) else
                "Branch return failed; history retained. If summary generation failed, try /return again.")
