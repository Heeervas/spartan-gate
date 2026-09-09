"""Discord-only voice signaling and UDP relay; no arbitrary datagram destinations."""
import asyncio
import hmac
import ipaddress
import json
import os
import re
import struct
import time
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, WSMsgType, TraceConfig, web

MAX_FRAME = 1024 * 1024
MAX_PACKET = 4096
MAX_SESSIONS = 1


def endpoint(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "wss" or parsed.username or parsed.password or parsed.fragment
            or parsed.path not in ("", "/") or parsed.query != "v=8"
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*\.discord\.media", parsed.hostname or "")
            or parsed.port not in (None, 443, 2048, 2053, 2083, 2087, 2096, 8443)):
        raise ValueError("Invalid Discord voice endpoint")
    return value


def ready_peer(data):
    address = ipaddress.ip_address(data["ip"])
    port, ssrc = data["port"], data["ssrc"]
    if (address.version != 4 or not address.is_global or address.is_multicast
            or type(port) is not int or not 1 <= port <= 65535
            or type(ssrc) is not int or not 0 <= ssrc <= 0xffffffff):
        raise ValueError("Invalid Discord voice destination")
    return str(address), port, ssrc


class Packets(asyncio.DatagramProtocol):
    def __init__(self, session, incoming):
        self.session, self.incoming = session, incoming
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, packet, address):
        s = self.session
        if s.closed or not 0 < len(packet) <= MAX_PACKET:
            return
        second = int(time.monotonic())
        if second != s.second:
            s.second, s.packet_count = second, 0
        s.packet_count += 1
        if s.packet_count > 256:
            return
        if self.incoming:
            if address[0] != s.client_ip:
                return
            if s.client_address is None:
                if len(packet) != 74 or struct.unpack_from(">HHI", packet) != (1, 70, s.ssrc):
                    return
                s.client_address = address
            if address != s.client_address or s.remote is None:
                return
            if s.remote.get_write_buffer_size() > 65536:
                return
            s.remote.sendto(packet)
            s.sent += len(packet)
        elif s.client_address and s.local:
            # The connected external UDP socket accepts only its upstream peer.
            if s.local.get_write_buffer_size() > 65536:
                return
            s.local.sendto(packet, s.client_address)
            s.received += len(packet)

    def error_received(self, exc):
        pass  # No raw exception/destination/audio logging.


class Session:
    def __init__(self, client_ip, bind_ip):
        self.client_ip, self.bind_ip = client_ip, bind_ip
        self.client_address = None
        self.local = self.remote = None
        self.ssrc = 0
        self.closed = False
        self.sent = self.received = self.packet_count = self.second = 0

    async def open(self, data):
        address, port, self.ssrc = ready_peer(data)
        self.close_udp()
        self.closed = False
        loop = asyncio.get_running_loop()
        try:
            self.remote, _ = await loop.create_datagram_endpoint(
                lambda: Packets(self, False), remote_addr=(address, port))
            self.local, _ = await loop.create_datagram_endpoint(
                lambda: Packets(self, True), local_addr=(self.bind_ip, 0))
        except BaseException:
            self.close_udp()
            raise
        return self.local.get_extra_info("sockname")

    def close_udp(self):
        for transport in (self.local, self.remote):
            if transport:
                transport.close()
        self.local = self.remote = None
        self.client_address = None

    def close(self):
        self.closed = True
        self.close_udp()


async def voice(request):
    config = request.app["config"]
    auth = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth.encode(), ("Bearer " + config["token"]).encode()):
        raise web.HTTPUnauthorized()
    try:
        upstream_url = endpoint(request.headers.get("X-Discord-Voice-Endpoint", ""))
        client_ip = str(ipaddress.ip_address(request.remote))
        if ipaddress.ip_address(client_ip) not in config["network"]:
            raise ValueError("Invalid client")
    except (ValueError, TypeError):
        raise web.HTTPForbidden()
    if len(request.app["sessions"]) >= MAX_SESSIONS:
        raise web.HTTPTooManyRequests()
    downstream = web.WebSocketResponse(max_msg_size=MAX_FRAME, heartbeat=20, compress=False)
    session = Session(client_ip, config["bind_ip"])
    task = asyncio.current_task()
    request.app["sessions"].add(task)
    pumps = []
    reason = "normal"
    try:
        async with asyncio.timeout(600):
            trace = TraceConfig()
            async def deny_redirect(*args):
                raise ValueError("Discord redirects are forbidden")
            trace.on_request_redirect.append(deny_redirect)
            async with ClientSession(timeout=ClientTimeout(total=25), trust_env=False, trace_configs=[trace]) as http:
                # TLS validation stays enabled. No upstream headers or client credentials copied.
                async with http.ws_connect(upstream_url, proxy=config["proxy"],
                                           max_msg_size=MAX_FRAME, heartbeat=20) as upstream:
                    if str(upstream._response.url) != upstream_url:
                        raise ValueError("Unexpected upstream URL")
                    await downstream.prepare(request)
                    async def copy(source, target, from_provider):
                        async for message in source:
                            if message.type == WSMsgType.TEXT:
                                event = json.loads(message.data)
                                if not isinstance(event, dict):
                                    raise ValueError("Invalid voice frame")
                                if from_provider and event.get("op") == 2:
                                    # Only the TLS-authenticated provider can authorize UDP.
                                    host, port = await session.open(event["d"])
                                    event["d"] = {**event["d"], "ip": host, "port": port}
                                    payload = json.dumps(event)
                                else:
                                    if not from_provider and event.get("op") == 7:
                                        raise ValueError("Prototype requires a fresh voice join")
                                    payload = message.data
                                await asyncio.wait_for(target.send_str(payload), 5)
                            elif message.type == WSMsgType.BINARY:
                                # Forward DAVE binary signaling without interpreting its contents.
                                await asyncio.wait_for(target.send_bytes(message.data), 5)
                            elif message.type == WSMsgType.ERROR:
                                raise ValueError("Voice transport failed")
                    pumps = [asyncio.create_task(copy(upstream, downstream, True)),
                             asyncio.create_task(copy(downstream, upstream, False))]
                    done, pending = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                    for finished in done:
                        finished.result()
    except asyncio.CancelledError:
        reason = "shutdown"
        raise
    except Exception:
        reason = "relay_failed"
        if not downstream.prepared:
            raise web.HTTPBadGateway(text="Discord voice relay unavailable") from None
    finally:
        for pump in pumps:
            pump.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        session.close()
        request.app["sessions"].discard(task)
        if downstream.prepared:
            try:
                await asyncio.wait_for(downstream.close(code=1000 if reason == "normal" else 1011), 3)
            except TimeoutError:
                pass
        print(json.dumps({"event":"voice_relay_closed", "reason":reason,
                          "udp_sent_bytes":session.sent,"udp_received_bytes":session.received}), flush=True)
    return downstream


async def health(request):
    return web.json_response({"ok":True,"sessions":len(request.app["sessions"])})


async def shutdown(app):
    tasks = list(app["sessions"])
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)


def application(config):
    app = web.Application(client_max_size=4096)
    app["config"], app["sessions"] = config, set()
    app.router.add_get("/voice", voice)
    app.router.add_get("/health", health)
    app.on_shutdown.append(shutdown)
    return app


def main():
    token = os.environ.get("DISCORD_VOICE_RELAY_TOKEN", "")
    bind_ip = os.environ["DISCORD_VOICE_RELAY_BIND_IP"]
    network = ipaddress.ip_network(os.environ["DISCORD_VOICE_RELAY_NETWORK"])
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token) or ipaddress.ip_address(bind_ip) not in network:
        raise ValueError("Invalid relay configuration")
    app = application({"token":token,"bind_ip":bind_ip,"network":network,
                       "proxy":"http://proxy:8888"})
    web.run_app(app, host=bind_ip, port=8791, access_log=None, print=None)


if __name__ == "__main__":
    main()
