import json
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name('browserless-cdp-broker.py')


def install_websockets_stub():
    client = types.SimpleNamespace(ClientConnection=object)
    server = types.SimpleNamespace(ServerConnection=object)
    asyncio_mod = types.SimpleNamespace(client=client, server=server)
    http11 = types.SimpleNamespace(Request=object, Response=lambda *args, **kwargs: object())
    datastructures = types.SimpleNamespace(Headers=list)
    exceptions = types.SimpleNamespace(ConnectionClosed=Exception)
    websockets = types.SimpleNamespace(
        connect=None,
        serve=None,
        exceptions=exceptions,
    )
    sys.modules.setdefault('websockets', websockets)
    sys.modules.setdefault('websockets.asyncio', asyncio_mod)
    sys.modules.setdefault('websockets.asyncio.client', client)
    sys.modules.setdefault('websockets.asyncio.server', server)
    sys.modules.setdefault('websockets.datastructures', datastructures)
    sys.modules.setdefault('websockets.http11', http11)


def load_module():
    install_websockets_stub()
    spec = importlib.util.spec_from_file_location('browserless_cdp_broker', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class BrowserlessCdpBrokerLockCleanupTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.profile = pathlib.Path(self.tmp.name) / 'main'
        self.profile.mkdir()
        for name in self.module.LOCK_FILE_NAMES:
            (self.profile / name).symlink_to('old-host-123')
        self._old_idle = self.module.browserless_is_idle

    def tearDown(self):
        self.module.browserless_is_idle = self._old_idle
        self.tmp.cleanup()

    def assert_locks_exist(self):
        for name in self.module.LOCK_FILE_NAMES:
            self.assertTrue((self.profile / name).is_symlink())

    def assert_locks_removed(self):
        for name in self.module.LOCK_FILE_NAMES:
            self.assertFalse((self.profile / name).exists())
            self.assertFalse((self.profile / name).is_symlink())

    def test_removes_singleton_locks_when_browserless_is_idle(self):
        self.module.browserless_is_idle = lambda: True

        self.assertTrue(self.module.remove_stale_profile_locks(self.profile, 'test'))

        self.assert_locks_removed()

    def test_keeps_singleton_locks_when_browserless_is_active(self):
        self.module.browserless_is_idle = lambda: False

        self.assertFalse(self.module.remove_stale_profile_locks(self.profile, 'test'))

        self.assert_locks_exist()

    def test_keeps_singleton_locks_when_status_check_fails(self):
        def fail():
            raise RuntimeError('status unavailable')

        self.module.browserless_is_idle = fail

        self.assertFalse(self.module.remove_stale_profile_locks(self.profile, 'test'))

        self.assert_locks_exist()


class BrowserlessCdpBrokerDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.broker = self.module.CDPBroker('ws://browserless:3000/chromium')

    def test_json_list_empty_without_targets(self):
        self.assertEqual(self.broker.discovery_payload('/json/list'), [])
        self.assertEqual(self.broker.discovery_payload('/json'), [])

    def test_target_created_appears_in_json_list(self):
        self.broker.update_targets_from_event({
            'method': 'Target.targetCreated',
            'params': {
                'targetInfo': {
                    'targetId': 'page-1',
                    'type': 'page',
                    'title': 'Example',
                    'url': 'https://example.com/',
                    'attached': False,
                },
            },
        })

        payload = self.broker.discovery_payload('/json/list')

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['id'], 'page-1')
        self.assertEqual(payload[0]['type'], 'page')
        self.assertEqual(payload[0]['title'], 'Example')
        self.assertEqual(payload[0]['url'], 'https://example.com/')
        self.assertEqual(payload[0]['webSocketDebuggerUrl'], self.module.DISCOVERY_WS_URL)

    def test_target_info_changed_replaces_about_blank_with_real_url(self):
        self.broker.update_targets_from_event({
            'method': 'Target.targetCreated',
            'params': {
                'targetInfo': {
                    'targetId': 'page-1',
                    'type': 'page',
                    'title': '',
                    'url': 'about:blank',
                },
            },
        })
        self.assertEqual(self.broker.discovery_payload('/json/list')[0]['url'], 'about:blank')

        self.broker.update_targets_from_event({
            'method': 'Target.targetInfoChanged',
            'params': {
                'targetInfo': {
                    'targetId': 'page-1',
                    'type': 'page',
                    'title': 'Loaded',
                    'url': 'https://loaded.example/',
                },
            },
        })

        payload = self.broker.discovery_payload('/json/list')
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['title'], 'Loaded')
        self.assertEqual(payload[0]['url'], 'https://loaded.example/')

    def test_non_blank_targets_are_preferred_over_about_blank(self):
        for target_id, url in [('blank', 'about:blank'), ('real', 'https://real.example/')]:
            self.broker.upsert_target({
                'targetId': target_id,
                'type': 'page',
                'title': target_id,
                'url': url,
            })

        payload = self.broker.discovery_payload('/json/list')

        self.assertEqual([entry['id'] for entry in payload], ['real'])
        self.assertEqual(payload[0]['url'], 'https://real.example/')

    def test_target_destroyed_disappears_from_json_list(self):
        self.broker.upsert_target({
            'targetId': 'page-1',
            'type': 'page',
            'title': 'Example',
            'url': 'https://example.com/',
        })
        self.broker.update_targets_from_event({
            'method': 'Target.targetDestroyed',
            'params': {'targetId': 'page-1'},
        })

        self.assertEqual(self.broker.discovery_payload('/json/list'), [])

    def test_get_targets_response_populates_cache(self):
        self.broker.update_targets_from_internal_response('Target.getTargets', {
            'id': 5,
            'result': {
                'targetInfos': [
                    {
                        'targetId': 'browser-1',
                        'type': 'browser',
                        'title': '',
                        'url': '',
                    },
                    {
                        'targetId': 'page-1',
                        'type': 'page',
                        'title': 'Example',
                        'url': 'https://example.com/',
                    },
                ],
            },
        })

        payload = self.broker.discovery_payload('/json/list')
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['id'], 'page-1')
        self.assertEqual(payload[0]['url'], 'https://example.com/')

    def test_json_version_points_to_broker_websocket(self):
        payload = self.broker.discovery_payload('/json/version')

        self.assertEqual(payload['Browser'], 'Spartan Browserless CDP Broker')
        self.assertEqual(payload['webSocketDebuggerUrl'], self.module.DISCOVERY_WS_URL)


class BrowserlessCdpBrokerNavigationGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_module()
        self.broker = self.module.CDPBroker('ws://browserless:3000/chromium')
        self.client = self.FakeClient()
        self.upstream_requested = False

        async def fail_if_called():
            self.upstream_requested = True
            raise AssertionError('blocked navigation should not open upstream')

        self.broker.ensure_upstream = fail_if_called

    class FakeClient:
        def __init__(self):
            self.messages = []

        async def send(self, payload):
            self.messages.append(payload)

    async def assert_navigation_blocked(self, method, url):
        await self.broker.forward_from_client(self.client, json.dumps({
            'id': 7,
            'method': method,
            'params': {'url': url},
        }))

        self.assertFalse(self.upstream_requested)
        self.assertEqual(len(self.client.messages), 1)
        payload = json.loads(self.client.messages[0])
        self.assertEqual(payload['id'], 7)
        self.assertEqual(payload['error']['code'], -32000)
        self.assertIn('http://hermes:<port>/...', payload['error']['message'])

    async def test_page_navigate_file_url_returns_cdp_error(self):
        await self.assert_navigation_blocked('Page.navigate', 'file:///opt/data/example.html')
        self.assertIn('local file URL', json.loads(self.client.messages[0])['error']['message'])

    async def test_target_create_target_file_url_returns_cdp_error(self):
        await self.assert_navigation_blocked('Target.createTarget', 'file:///opt/data/example.html')

    async def test_page_navigate_localhost_returns_cdp_error(self):
        await self.assert_navigation_blocked('Page.navigate', 'http://localhost:8765/example.html')
        self.assertIn('Hermes-local URL', json.loads(self.client.messages[0])['error']['message'])

    async def test_page_navigate_127_0_0_1_returns_cdp_error(self):
        await self.assert_navigation_blocked('Page.navigate', 'http://127.0.0.1:8765/example.html')

    async def test_blocked_session_command_preserves_session_id(self):
        await self.broker.forward_from_client(self.client, json.dumps({
            'id': 8,
            'sessionId': 'session-1',
            'method': 'Page.navigate',
            'params': {'url': 'file:///opt/data/example.html'},
        }))

        payload = json.loads(self.client.messages[0])
        self.assertEqual(payload['id'], 8)
        self.assertEqual(payload['sessionId'], 'session-1')

    async def test_public_and_hermes_urls_are_not_blocked(self):
        self.assertIsNone(self.module.blocked_navigation_reason({
            'method': 'Page.navigate',
            'params': {'url': 'https://example.com/'},
        }))
        self.assertIsNone(self.module.blocked_navigation_reason({
            'method': 'Page.navigate',
            'params': {'url': 'http://hermes:8765/example.html'},
        }))


if __name__ == '__main__':
    unittest.main()
