import asyncio
import importlib.util
import ipaddress
import json
from types import SimpleNamespace
from pathlib import Path
import struct
import unittest
from unittest.mock import patch, AsyncMock

from aiohttp import web, ClientSession
from server import endpoint, ready_peer, application, Session, Packets


class PolicyTests(unittest.TestCase):
 def test_endpoint_exact_domain_tls_and_ports(self):
  self.assertEqual(endpoint('wss://c-mad06-abcd.discord.media:2096/?v=8'),'wss://c-mad06-abcd.discord.media:2096/?v=8')
  for value in ['ws://c-mad06.discord.media/?v=8','wss://discord.media.evil.test/?v=8','wss://127.0.0.1/?v=8','wss://a@c-mad06.discord.media/?v=8','wss://c-mad06.discord.media:22/?v=8','wss://c-mad06.discord.media/path?v=8']:
   with self.subTest(value=value),self.assertRaises(ValueError): endpoint(value)

 def test_provider_destination_rejects_nonpublic_and_bad_values(self):
  for ip in ['127.0.0.1','169.254.169.254','10.0.0.1','224.0.0.1','::1']:
   with self.subTest(ip=ip),self.assertRaises(ValueError): ready_peer({'ip':ip,'port':50000,'ssrc':7})
  for port in [True,0,65536,'443']:
   with self.assertRaises(ValueError): ready_peer({'ip':'8.8.8.8','port':port,'ssrc':7})


class Echo(asyncio.DatagramProtocol):
 def connection_made(self,t): self.transport=t
 def datagram_received(self,data,addr): self.transport.sendto(data,addr)


class RelayTests(unittest.IsolatedAsyncioTestCase):
 async def test_real_loopback_udp_both_directions_and_close(self):
  loop=asyncio.get_running_loop()
  echo,_=await loop.create_datagram_endpoint(Echo,local_addr=('127.0.0.1',0))
  session=Session('127.0.0.1','127.0.0.1')
  try:
   # Production public-IP restriction is relaxed ONLY for this local fixture.
   with patch('server.ready_peer',return_value=(*echo.get_extra_info('sockname'),7)):
    relay=await session.open({})
   import socket
   sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.setblocking(False)
   self.addCleanup(sock.close)
   await loop.sock_connect(sock,relay)
   discovery=struct.pack('>HHI',1,70,7)+bytes(66)
   await loop.sock_sendall(sock,discovery)
   self.assertEqual(await asyncio.wait_for(loop.sock_recv(sock,4096),2),discovery)
   audio=b'opaque-encrypted-audio'
   await loop.sock_sendall(sock,audio)
   self.assertEqual(await asyncio.wait_for(loop.sock_recv(sock,4096),2),audio)
   session.close();session.close()
   self.assertIsNone(session.local);self.assertIsNone(session.remote)
  finally: session.close();echo.close()

 async def test_no_auth_and_bad_target_never_open_upstream(self):
  cfg={'token':'x'*48,'network':ipaddress.ip_network('127.0.0.0/8'),'bind_ip':'127.0.0.1','proxy':'http://proxy:8888'}
  runner=web.AppRunner(application(cfg));await runner.setup()
  site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/voice'
  try:
   async with ClientSession() as client:
    async with client.get(url) as r:self.assertEqual(r.status,401)
    async with client.get(url,headers={'Authorization':'Bearer '+'x'*48,'X-Discord-Voice-Endpoint':'wss://evil.test/?v=8'}) as r:self.assertEqual(r.status,403)
   self.assertEqual(len(runner.app['sessions']),0)
  finally:await runner.cleanup()

 async def test_signaling_rewrites_only_provider_ready_and_preserves_binary(self):
  target='wss://c-mad06-test.discord.media:2096/?v=8'
  class Upstream:
   def __init__(self):
    self.queue=asyncio.Queue();self.sent=[];self._response=SimpleNamespace(url=target)
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
   def __aiter__(self):return self
   async def __anext__(self):return await self.queue.get()
   async def send_str(self,v):self.sent.append(v)
   async def send_bytes(self,v):self.sent.append(v)
  upstream=Upstream()
  class HTTP:
   def __init__(self,*args,**kwargs):pass
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
   def ws_connect(self,url,**kwargs):
    self_url=url
    assert self_url==target and kwargs['proxy']=='http://proxy:8888'
    return upstream
  cfg={'token':'x'*48,'network':ipaddress.ip_network('127.0.0.0/8'),'bind_ip':'127.0.0.1','proxy':'http://proxy:8888'}
  runner=web.AppRunner(application(cfg));await runner.setup()
  site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/voice'
  opened=AsyncMock(return_value=('127.0.0.1',30000))
  try:
   with patch('server.ClientSession',HTTP),patch.object(Session,'open',opened):
    async with ClientSession() as client:
     async with client.ws_connect(url,headers={'Authorization':'Bearer '+'x'*48,'X-Discord-Voice-Endpoint':target}) as ws:
      await ws.send_json({'op':2,'d':{'ip':'evil','port':123,'ssrc':7}})
      await asyncio.sleep(0.02)
      opened.assert_not_awaited()
      ready={'op':2,'d':{'ip':'8.8.8.8','port':50000,'ssrc':7,'modes':['test']}}
      await upstream.queue.put(SimpleNamespace(type=1,data=json.dumps(ready)))
      reply=await asyncio.wait_for(ws.receive_json(),2)
      self.assertEqual(reply['d']['ip'],'127.0.0.1');self.assertEqual(reply['d']['port'],30000)
      self.assertEqual(reply['d']['ssrc'],7)
      await upstream.queue.put(SimpleNamespace(type=2,data=b'opaque-dave-frame'))
      self.assertEqual((await asyncio.wait_for(ws.receive(),2)).data,b'opaque-dave-frame')
      await ws.send_bytes(b'client-dave-frame');await asyncio.sleep(0.02)
      self.assertIn(b'client-dave-frame',upstream.sent)
    await asyncio.sleep(0.02)
    self.assertEqual(len(runner.app['sessions']),0)
  finally:await runner.cleanup()

 def test_wrong_source_and_bad_discovery_are_not_forwarded(self):
  s=Session('172.29.0.9','172.29.0.10');s.ssrc=7
  protocol=Packets(s,True)
  protocol.datagram_received(struct.pack('>HHI',1,70,7)+bytes(66),('172.29.0.8',1234))
  self.assertIsNone(s.client_address)
  protocol.datagram_received(b'arbitrary',('172.29.0.9',1234))
  self.assertIsNone(s.client_address)

 def test_packet_size_and_sender_port_are_bounded(self):
  from unittest.mock import Mock
  session=Session('172.29.0.9','172.29.0.10');session.ssrc=7
  session.remote=Mock();session.remote.get_write_buffer_size.return_value=0
  protocol=Packets(session,True)
  discovery=struct.pack('>HHI',1,70,7)+bytes(66)
  protocol.datagram_received(discovery,('172.29.0.9',1234))
  session.remote.sendto.assert_called_once_with(discovery)
  session.remote.sendto.reset_mock()
  protocol.datagram_received(b'x'*4097,('172.29.0.9',1234))
  protocol.datagram_received(b'audio',('172.29.0.9',1235))
  session.remote.sendto.assert_not_called()

 async def test_invalid_provider_peer_opens_no_socket(self):
  session=Session('127.0.0.1','127.0.0.1')
  with patch.object(asyncio.get_running_loop(),'create_datagram_endpoint',new_callable=AsyncMock) as create:
   with self.assertRaises(ValueError):await session.open({'ip':'127.0.0.1','port':53,'ssrc':7})
   create.assert_not_awaited()



class ClientTests(unittest.IsolatedAsyncioTestCase):
 async def test_only_voice_is_redirected_and_missing_config_denied(self):
  spec=importlib.util.spec_from_file_location('adapter',Path(__file__).parents[1]/'hermes-plugins/spartan-discord-voice-relay/__init__.py')
  m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  class HTTP:
   async def ws_connect(self,url,*,compress=0):return 'normal'
  m.install(HTTP);m.install(HTTP)
  self.assertEqual(await HTTP().ws_connect('wss://gateway.discord.gg'),'normal')
  with patch.dict('os.environ',{},clear=True):
   with self.assertRaises(RuntimeError):await HTTP().ws_connect('wss://c-mad06.discord.media/?v=8')

 async def test_voice_uses_internal_relay_without_proxy_or_fallback(self):
  spec=importlib.util.spec_from_file_location('adapter_live_shape',Path(__file__).parents[1]/'hermes-plugins/spartan-discord-voice-relay/__init__.py')
  m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  session=SimpleNamespace(ws_connect=AsyncMock(return_value='relay-ws'))
  class HTTP:
   def __init__(self):self._HTTPClient__session=session
   async def ws_connect(self,*args,**kwargs):raise AssertionError('Direct fallback forbidden')
  m.install(HTTP)
  env={'DISCORD_VOICE_RELAY_URL':'http://discord-voice-relay:8791/voice','DISCORD_VOICE_RELAY_TOKEN':'synthetic_'+'x'*40}
  target='wss://c-mad06-test.discord.media:2096/?v=8'
  with patch.dict('os.environ',env,clear=True):
   self.assertEqual(await HTTP().ws_connect(target),'relay-ws')
   kw=session.ws_connect.call_args.kwargs
   self.assertIsNone(kw['proxy']);self.assertEqual(kw['headers']['X-Discord-Voice-Endpoint'],target)
   session.ws_connect.side_effect=OSError('synthetic secret must not escape')
   with self.assertRaisesRegex(RuntimeError,'no direct fallback') as caught:await HTTP().ws_connect(target)
   self.assertNotIn('synthetic secret',str(caught.exception))


if __name__=='__main__':unittest.main()
