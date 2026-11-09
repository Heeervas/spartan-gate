"""Desktop adapters for the existing branch protocol; no authority/legacy fallback.

The native durable admission request ID binds the temporary execution restriction.
Navigation status is a duplicate fence, not a crash-safe exactly-once ledger.
"""
import asyncio
from contextlib import contextmanager
from functools import partial
import threading
import time

import spartan_branch_navigation as nav

_REQUEST_PREFIX = 'spartan-return/'


def authority_handlers(connection):
    return {'session.branch_return': partial(authority_rpc, connection, False),
            'session.branch_cancel': partial(authority_rpc, connection, True)}


async def authority_rpc(connection, cancel, ref, params):
    from gateway.session_llm_oneshot import DeferredReply
    from hermes_state_runtime import RuntimeStoreError
    if (set(params) - {'session_id', 'profile'}
            or not isinstance(ref.session_id, str) or not ref.session_id):
        raise RuntimeStoreError('invalid_params')
    loop = asyncio.get_running_loop()
    # Reuse the native bounded deferred-reply pool; never park the socket's serial lane.
    return DeferredReply(lambda: asyncio.run_coroutine_threadsafe(
        authority_navigate(connection, ref, cancel=cancel), loop).result())


def _authorized_idle(connection, ref):
    from gateway.config import Platform
    from hermes_state_mutation_guards import require_idle
    from hermes_state_runtime import RuntimeStoreError
    authority = connection.authority
    authority._require_admission_open()
    if ref.session_id not in connection.subscriptions:
        raise RuntimeStoreError('permission_denied')
    authority.authorize(connection.actor, ref, 'session:control')
    authority.authorize(connection.actor, ref, 'session:submit')
    if authority.sessions[ref.session_id].source.platform != Platform.LOCAL:
        raise RuntimeStoreError('permission_denied')
    with authority.db._read_ctx() as conn:
        require_idle(authority.db, conn, [ref.session_id])


async def authority_navigate(connection, ref, *, cancel=False):
    """One owner-bound native resume and Submission; failure never instructs UI navigation."""
    from gateway.session_contract import SessionRef, Submission
    from gateway.session_llm_oneshot import _session_runtime
    from gateway.session_runtime_workers import track_mutation
    from hermes_state_mutation_guards import MUTATION_GUARD_REFUSALS, busy_refusal
    from hermes_state_runtime import RuntimeStoreError
    authority = connection.authority

    async def work():
        _authorized_idle(connection, ref)
        async with authority.sessions[ref.session_id].mutation_lock:
            _authorized_idle(connection, ref)
            db = authority.db
            owner, metadata = nav.navigation_owner(db, ref.session_id)
            nav.require_open(metadata)
            parent = SessionRef(ref.profile_id, metadata['parent'])
            # Native resume authorizes the actual principal/profile and attaches this viewer.
            await connection.resume(parent, {})
            _authorized_idle(connection, parent)
            if cancel:
                nav.set_status(db, owner, metadata, 'cancelled')
                return {'status': 'cancelled', 'parent_session_id': parent.session_id}
            runtime = _session_runtime(authority, ref)
            owner, metadata = await asyncio.to_thread(lambda: nav.begin_return(db, ref.session_id, runtime()))
            try:
                _authorized_idle(connection, ref)
                _authorized_idle(connection, parent)
                # The existing admission transaction rechecks idle after any concurrent writer.
                from hermes_state_mutation_guards import require_idle
                def guard(conn):
                    require_idle(db, conn, [ref.session_id, parent.session_id])
                request = Submission(_REQUEST_PREFIX + owner + '/' + metadata['return_id'], parent,
                                     {'text': nav.return_prompt(metadata)}, 'queue')
                metadata = nav.set_status(db, owner, metadata, 'queued')
                receipt = await authority.submit(connection.actor, request, _authorize_write=guard)
            except BaseException:
                # Admission may have committed before transport/storage failed: never reopen blindly.
                nav.set_status(db, owner, metadata, 'ambiguous')
                raise
            return await _await_ack(connection, parent, owner, metadata, receipt)

    try:
        return await asyncio.shield(track_mutation(authority, work()))
    except nav.NavigationError as exc:
        raise RuntimeStoreError('branch_navigation_refused', detail=str(exc)) from exc
    except MUTATION_GUARD_REFUSALS as exc:
        raise busy_refusal(exc) from exc
    except RuntimeStoreError:
        raise
    except Exception as exc:
        raise RuntimeStoreError('branch_navigation_refused', detail='Branch navigation failed; inspect parent history before retrying.') from exc


async def _await_ack(connection, parent, owner, metadata, receipt):
    from gateway.session_results import admission_result
    from hermes_state_runtime import RuntimeStoreError
    db = connection.authority.db
    deadline = asyncio.get_running_loop().time() + 600
    while receipt.status in {'queued', 'started'}:
        if asyncio.get_running_loop().time() >= deadline:
            nav.set_status(db, owner, metadata, 'ambiguous')
            raise RuntimeStoreError('branch_navigation_refused', detail='Acknowledgment pending; inspect parent history.')
        await asyncio.sleep(.05)
        receipt = await connection.authority.receipt(connection.actor, parent, receipt.admission_id)
    saved = admission_result(db, receipt.admission_id)
    success = receipt.outcome == 'completed' and saved is not None and nav.successful_ack(saved['result'])
    nav.set_status(db, owner, metadata, 'completed' if success else 'failed')
    if not success:
        raise RuntimeStoreError('branch_navigation_refused', detail='Parent acknowledgment failed; inspect its history. Not importing again.')
    return {'status': 'completed', 'parent_session_id': parent.session_id}


@contextmanager
def admission_ack_scope(authority, ref, row):
    """Only the exact server-prepared admission disables tools, at execution (incl. native worker thread)."""
    request = row.get('request_id', '')
    if not request.startswith(_REQUEST_PREFIX):
        yield
        return
    from hermes_state_runtime import RuntimeStoreError
    parts = request[len(_REQUEST_PREFIX):].split('/')
    if len(parts) != 2:
        raise RuntimeStoreError('permission_denied')
    owner, return_id = parts
    metadata = authority.db.get_session_model_config_value(owner, nav.NAV_KEY) or {}
    if (metadata.get('return_id') != return_id or metadata.get('parent') != ref.session_id
            or metadata.get('status') not in {'queued', 'running', 'ambiguous'}
            or row['payload'].get('text') != nav.return_prompt(metadata)):
        raise RuntimeStoreError('permission_denied')
    nav.set_status(authority.db, owner, metadata, 'running')
    with nav.acknowledgment_scope(True):
        yield


def register_legacy(server):
    server.register_method('session.branch_return', partial(legacy_navigate, server, False))
    server.register_method('session.branch_cancel', partial(legacy_navigate, server, True))


def _legacy_idle(server, rid, sid, session):
    transport = server.current_transport() or server._stdio_transport
    if (not server._session_transport_contains(session, transport)
            or server._session_auth_user_id(session) != server._transport_auth_user_id(transport)):
        raise nav.NavigationError('The session belongs to another connection owner.')
    if session.get('running') or session.get('_closing') or session.get('_spartan_branch_ack'):
        raise nav.NavigationError('Finish or stop the active turn before navigating.')
    refusal = server._reattach_refusal(rid, sid, session)
    if refusal:
        raise nav.NavigationError('The session belongs to another connection owner.')
    if server._session_uses_compute_host(session, server._load_dashboard_process_isolation_config()):
        raise nav.NavigationError('Legacy isolated workers do not support branch navigation.')


def legacy_navigate(server, cancel, rid, params):
    """Compatibility sidecar uses its own scoped lookup/resume/prompt machinery, never a slash worker."""
    if set(params) - {'session_id', 'profile'}:
        return server._err(rid, 4000, 'Invalid branch navigation parameters.')
    session, error = server._sess(params, rid)
    if error:
        return error
    sid = params.get('session_id', '')
    try:
        if params.get('profile') and not server._live_profile_matches(session, server._profile_home(params['profile'])):
            raise nav.NavigationError('The session belongs to another profile.')
        _legacy_idle(server, rid, sid, session)
        with server._session_db(session) as db:
            if db is None:
                raise nav.NavigationError('Session database unavailable.')
            owner, metadata = nav.navigation_owner(db, session['session_key'])
            nav.require_open(metadata)
            resumed = server._methods['session.resume'](rid, {'session_id': metadata['parent'],
                'profile': server.profile_name_for_home(session.get('profile_home'))})
            if 'error' in resumed:
                return resumed
            parent_sid = resumed['result']['session_id']
            parent, error = server._sess({'session_id': parent_sid}, rid)
            if error:
                return error
            _legacy_idle(server, rid, parent_sid, parent)
            if cancel:
                nav.set_status(db, owner, metadata, 'cancelled')
                return server._ok(rid, dict(status='cancelled', parent_session_id=metadata['parent']))
            with session['history_lock']:
                _legacy_idle(server, rid, sid, session)
                owner, metadata = nav.begin_return(db, session['session_key'], server._main_runtime_from_agent(session['agent']))
            return _legacy_submit(server, rid, db, parent_sid, parent, owner, metadata)
    except Exception as exc:
        text = str(exc) if isinstance(exc, nav.NavigationError) else 'Branch navigation failed; inspect parent history.'
        return server._err(rid, 4091, text)


def _legacy_submit(server, rid, db, sid, parent, owner, metadata):
    from hermes_cli.input_sanitize import sanitize_user_prompt_text
    text = sanitize_user_prompt_text(nav.return_prompt(metadata))
    marker = {'text': text, 'done': threading.Event(), 'result': None}
    with parent['history_lock']:
        if parent.get('running') or parent.get('_spartan_branch_ack'):
            nav.set_status(db, owner, metadata, 'open')
            raise nav.NavigationError('Parent is busy; nothing imported.')
        parent['_spartan_branch_ack'] = marker
    nav.set_status(db, owner, metadata, 'queued')
    try:
        response = server._methods['prompt.submit'](rid, {'session_id': sid, 'text': text})
        if 'error' in response:
            nav.set_status(db, owner, metadata, 'ambiguous')
            return response
        if not marker['done'].wait(600):
            raise nav.NavigationError('Acknowledgment pending; inspect parent history. Not importing again.')
        deadline = time.monotonic() + 10
        while parent.get('running') and time.monotonic() < deadline:
            time.sleep(.01)
        success = nav.successful_ack(marker['result']) and not parent.get('running')
        nav.set_status(db, owner, metadata, 'completed' if success else 'failed')
        if not success:
            raise nav.NavigationError('Acknowledgment failed; inspect parent history. Not importing again.')
        return server._ok(rid, dict(status='completed', parent_session_id=metadata['parent']))
    except BaseException:
        current = db.get_session_model_config_value(owner, nav.NAV_KEY)
        if current['status'] == 'queued':
            nav.set_status(db, owner, metadata, 'ambiguous')
        raise
    finally:
        # A timed-out worker must retain its restriction until it actually finishes.
        if marker['done'].is_set() or not parent.get('running'):
            parent.pop('_spartan_branch_ack', None)


@contextmanager
def legacy_ack_turn(agent, session, text):
    marker = session.get('_spartan_branch_ack')
    enabled = marker is not None and marker['text'] == text
    try:
        with nav.acknowledgment_turn(agent, enabled=enabled):
            yield
    except BaseException:
        if enabled:
            marker['done'].set()
        raise


def legacy_ack_result(session, text, result):
    marker = session.get('_spartan_branch_ack')
    if marker is not None and marker['text'] == text:
        marker['result'] = result
        marker['done'].set()
