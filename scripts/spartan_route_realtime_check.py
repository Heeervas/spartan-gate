#!/usr/bin/env python3
"""Real Route acceptance: --stage uses existing in-container auth; --check uses env.

Default invocation is offline. No credentials are generated, printed or copied.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit


def report_base():
    return {"success": False, "mode": "route_acceptance", "checks": [],
            "stage": "local_preflight", "billing": "unknown"}


async def check(base, token, report):
    from aiohttp import ClientSession, ClientTimeout
    from spartan_route_realtime_probe import Wire, session_payload, ProbeFailure
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ProbeFailure("invalid_route_base_url")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "spartan-route"}:
        raise ProbeFailure("route_tls_required")
    if not token:
        raise ProbeFailure("route_client_credential_required")
    async with ClientSession(trust_env=False, timeout=ClientTimeout(total=30)) as client:
        report["stage"] = "local_negative_auth"
        async with client.post(base + '/v1/realtime/client_secrets', json={"session": session_payload()},
                               allow_redirects=False) as response:
            if response.status != 401:
                raise ProbeFailure("local_auth_denial_failed")
        report["checks"].append("local_invalid_auth_denied")
        report["stage"] = "route_mint"
        async with client.post(base + '/v1/realtime/client_secrets', json={"session": session_payload()},
                               headers={"Authorization": "Bearer " + token}, allow_redirects=False) as response:
            value = await response.json()
            if response.status != 200:
                # Report only fixed local HTTP status, never arbitrary server messages.
                raise ProbeFailure("route_mint_failed", response.status)
        ticket = value.get('value')
        if not isinstance(ticket, str) or not ticket or not isinstance(value.get('expires_at'), int):
            raise ProbeFailure('invalid_route_ticket')
        report['checks'].append('route_mint_accepted')
        report['stage'] = 'route_websocket'
        wsbase = base.replace('https:', 'wss:', 1).replace('http:', 'ws:', 1)
        async with client.ws_connect(wsbase + '/v1/realtime?model=gpt-realtime-2.1',
                                     headers={'Authorization': 'Bearer ' + ticket}, heartbeat=20) as ws:
            ticket = None
            wire = Wire(ws, report)
            await wire.exercise()
            report['stage'] = 'cancel_during_output'
            await ws.send_json({'type': 'response.create', 'event_id': 'acceptance-long-response', 'response': {
                'tool_choice': 'none', 'instructions': 'Count aloud from one to fifty slowly.', 'max_output_tokens': 1024}})
            created = await wire.until('response.created')
            response_id = created['response']['id']
            delta = await wire.until('response.output_audio.delta')
            if delta.get('response_id') != response_id:
                raise ProbeFailure('cancel_response_mismatch')
            started = time.monotonic()
            await ws.send_json({'type': 'response.cancel', 'response_id': response_id, 'event_id': 'acceptance-cancel'})
            done = await wire.until('response.done')
            if done.get('response', {}).get('id') != response_id or done['response'].get('status') != 'cancelled':
                raise ProbeFailure('cancellation_not_observed')
            report['cancel_terminal_ms'] = round((time.monotonic() - started) * 1000)
            report['checks'].append('cancelled_during_audio_output')
            await ws.send_json({'type': 'conversation.item.truncate', 'item_id': delta['item_id'],
                                'content_index': delta.get('content_index', 0), 'audio_end_ms': 0})
            await wire.until('conversation.item.truncated')
            report['checks'].append('playback_truncation_acknowledged')
            report['stage'] = 'next_turn'
            await ws.send_json({'type': 'conversation.item.create', 'item': {'type': 'message', 'role': 'user',
                                'content': [{'type': 'input_text', 'text': 'Say ready.'}]}})
            await ws.send_json({'type': 'response.create', 'response': {'tool_choice': 'none', 'max_output_tokens': 128}})
            await wire.response()
            report['checks'].append('next_turn_audio_completed')
            report['stage'] = 'close'
            await ws.close()
            if ws.close_code != 1000:
                raise ProbeFailure('unclean_route_close')
            report['checks'].append('route_clean_close')


async def inside(slot_index, report):
    from aiohttp import ClientSession, ClientTimeout, web
    from spartan_route import codex, realtime as rt
    from spartan_route_realtime_probe import checked_slot, bounded_json, selected_slot, ProbeFailure
    token = os.environ.get('CLAWROUTE_TOKEN', '').strip()
    if (Path(codex.__file__).resolve() != Path('/app/spartan_route/codex.py')
            or codex.CODEX_AUTH_STORE_ROOT != Path('/app/data/codex-auth')
            or os.environ.get('OPENAI_CODEX_TOKEN') or not token):
        raise ProbeFailure('existing_route_runtime_required')
    async with ClientSession(trust_env=True, timeout=ClientTimeout(total=25)) as http:
        router = codex.CodexRouter(http, None)
        slot = selected_slot(router, slot_index)
        try:
            async with ClientSession(trust_env=False, timeout=ClientTimeout(total=5)) as local:
                port = os.environ.get('CLAWROUTE_PORT', '18790')
                if not port.isdecimal():
                    raise ProbeFailure('local_route_port_invalid')
                async with local.get('http://127.0.0.1:' + port + '/api/codex/balancer',
                                     headers={'Authorization': 'Bearer ' + token}, allow_redirects=False) as response:
                    if response.status != 200:
                        raise ProbeFailure('local_status_unavailable', response.status)
                    status = checked_slot(await bounded_json(response), slot_index, slot.path)
            # Snapshot the existing service's policy/cooldowns; do not initialize
            # or migrate its database in the isolated process.
            override = {'routingEnabled': status['requestedRoutingEnabled'], 'accountKey': status.get('accountKey'),
                        'slotPathHash': status.get('slotPathHash'), 'updatedAt': status.get('routingOverrideUpdatedAt')}
            router.telemetry = SimpleNamespace(slot_routing_override=lambda index: override)
            slot.rate_limited_until = status['rateLimitedUntil']
            slot.transient_until = status['transientUntil']
            slot.auth_retry_at = status.get('authRetryAt') or 0
            @web.middleware
            async def middleware(request, handler):
                return await rt.voice_dispatch(request, handler)
            app = web.Application(middlewares=[middleware])
            # Reuse the existing local Route credential for this loopback-only
            # test server. No text/admin routes and no new credential are created.
            # Production config explicitly rejects reusing the global text key.
            cfg = rt.RealtimeConfig(enabled=True, clients=(rt.VoiceClient('staging', rt.digest(token), slot_index),))
            service = rt.register(app, cfg)
            await service.start(router)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            try:
                address = runner.addresses[0]
                await check('http://127.0.0.1:' + str(address[1]), token, report)
            finally:
                await service.shutdown()
                await runner.cleanup()
                report['reservations_remaining'] = len(service.pool.entries)
                report['tickets_remaining'] = len(service.pool.tickets)
        finally:
            await router.cleanup()


def stage_bundle(slot):
    root = Path(__file__).resolve().parents[1]
    sources = [('spartan_route_realtime_probe', root / 'scripts/spartan_route_realtime_probe.py'),
               ('spartan_route.realtime', root / 'infra/spartan-route/spartan_route/realtime.py')]
    program = 'import sys, types, spartan_route\n'
    for name, path in sources:
        program += f'm=types.ModuleType({name!r}); m.__file__={str(path.name)!r}; sys.modules[{name!r}]=m\n'
        program += f'exec(compile({path.read_text()!r}, m.__file__, "exec"), m.__dict__)\n'
    program += f'sys.argv=["realtime-check", "--inside", "--slot", {str(slot)!r}, "--approved-account-use"]\n'
    program += f'exec(compile({Path(__file__).read_text()!r}, "realtime-check", "exec"), {{"__name__":"__main__"}})\n'
    result = subprocess.run(['bash', '-c', 'source scripts/aliases.sh\nsg exec -T spartan-route python3 -'],
                            cwd=root, input=program, text=True)
    return result.returncode


async def run(args):
    from spartan_route_realtime_probe import ProbeFailure
    report = report_base()
    started = time.monotonic()
    try:
        async with asyncio.timeout(60):
            if args.inside:
                await inside(args.slot, report)
            else:
                await check(os.environ.get('SPARTAN_REALTIME_BASE_URL', ''), os.environ.get('SPARTAN_REALTIME_TOKEN', ''), report)
        report['success'] = True
        report['stage'] = 'complete'
    except ProbeFailure as exc:
        report['error'] = exc.code
        if exc.status is not None:
            report['http_status'] = exc.status
    except TimeoutError:
        report['error'] = 'acceptance_deadline'
    except Exception:
        report['error'] = 'acceptance_internal_or_transport_error'
    report['elapsed_ms'] = round((time.monotonic() - started) * 1000)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['success'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--stage', action='store_true')
    modes.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    modes.add_argument('--check', action='store_true')
    parser.add_argument('--slot', type=int)
    parser.add_argument('--approved-account-use', action='store_true')
    args = parser.parse_args()
    if not (args.stage or args.inside or args.check):
        print(json.dumps({'mode': 'offline', 'authorization_required': True,
                          'stage': 'loopback-only temporary service in existing Route container',
                          'check_env': ['SPARTAN_REALTIME_BASE_URL', 'SPARTAN_REALTIME_TOKEN']}))
        return 0
    if not args.approved_account_use or ((args.stage or args.inside) and (args.slot is None or args.slot < 0)):
        print(json.dumps({'error': 'explicit_account_authorization_required'}))
        return 2
    return stage_bundle(args.slot) if args.stage else asyncio.run(run(args))


if __name__ == '__main__':
    raise SystemExit(main())
