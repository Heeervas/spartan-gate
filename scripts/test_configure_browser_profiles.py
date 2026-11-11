"""Regression tests for real files and the operator API transport/recovery path."""
import base64
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from scripts import configure_browser_profiles as profiles
from scripts.browser_profile_hub import Hub, HubError
from scripts.configure_mcphub import CLOSED_SERVER_ID, NAME, encode


class StaticHub(Hub):
    def __init__(self):
        self.value = {'mcpServers': {'legacy-browser': {'id': 'legacy'}, 'shared': {'id': 'shared'}},
            'groups': [{'id': 'agents-id', 'name': 'agents', 'owner': 'admin', 'servers': [
                {'name': 'legacy-browser', 'tools': ['browser_session_create'], 'prompts': [], 'resources': []},
                {'name': 'shared', 'tools': ['common_action'], 'prompts': [], 'resources': []}]}],
            'systemConfig': {'routing': {'skipAuth': False, 'enableBearerAuth': True,
                'enableGlobalRoute': False, 'enableGroupNameRoute': True}, 'toolPolicies': [
                {'id': 'closed', 'effect': 'allow', 'groupId': 'agents-id', 'serverId': CLOSED_SERVER_ID},
                {'id': 'common', 'effect': 'allow', 'groupId': 'agents-id', 'serverId': 'shared', 'tool': 'common_action'},
                {'id': 'old', 'effect': 'allow', 'groupId': 'agents-id', 'serverId': 'legacy', 'tool': 'browser_session_create'}]}}
        self.rows, self.calls = [], []

    def settings(self):
        return copy.deepcopy(self.value)

    def keys(self):
        return copy.deepcopy(self.rows)

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        assert method == 'PUT' and path.startswith('/api/auth/keys/')
        row = next(row for row in self.rows if path.endswith(row['id']))
        row['enabled'] = body['enabled']
        return copy.deepcopy(row)


class ProfileFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.hub = StaticHub()
        runtime = self.root / 'runtime'; runtime.mkdir(mode=0o700)
        for name in profiles.MODULES:
            self.put(runtime / name, (profiles.ROOT / 'infra/mcphub' / name).read_bytes())
        template = self.root / 'template.json'
        self.put(template, encode({'baseUrl': 'http://camofox:9377', 'token': 'synthetic-native-key', 'uploadRoot': '/browser/uploads'}))
        policy = self.root / 'policy.json'
        self.put(policy, encode({'version': 1, 'clients': [{'principal': 'legacy', 'browserUser': 'legacy-scratch',
            'tokenSha256': profiles.digest(('a' * 64).encode()), 'capabilities': ['browser', 'upload']}]}))
        self.manifest = {'hub': 'http://127.0.0.1:18793', 'passwordFile': str(self.root / 'password'),
            'sourceGroup': 'agents', 'legacyBrowser': 'legacy-browser', 'policyFile': str(policy),
            'nativeTemplate': str(template), 'nativeTemplateSha256': profiles.digest(template.read_bytes()),
            'runtimeHost': str(runtime), 'runtimeContainer': '/state/runtime',
            'runtimeSha256': {name: profiles.digest((runtime / name).read_bytes()) for name in profiles.MODULES},
            'bindingsHost': str(self.root / 'bindings'), 'bindingsContainer': '/state/bindings',
            'restBase': 'http://tool-access:9081', 'clientBase': 'http://caddy:18793',
            'tools': ['browser_session_create'], 'profiles': []}
        for index in range(2):
            directory = self.root / ('native-home-' + str(index)); directory.mkdir(mode=0o700)
            path = directory / 'config.yaml'
            self.put(path, b'# keep comment\nmodel: untouched\nmcp_servers:\n  spartan-mcphub:\n    url: http://caddy:18793/mcp/agents\n    headers:\n      Authorization: Bearer shared-fixture-key\n  foreign:\n    command: keep-me\n')
            path.chmod(0o644 if index else 0o600)
            self.manifest['profiles'].append({'name': 'fixture-' + str(index), 'slug': 'p' + str(index),
                'config': str(path), 'nativeUser': 'native-' + str(index), 'sha256': profiles.digest(path.read_bytes())})

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def put(path, raw):
        path.write_bytes(raw); path.chmod(0o600)

    def prepare(self):
        return profiles.preflight(self.manifest, self.hub)

    def test_all_targets_preflight_before_writes_and_no_implicit_opt_in(self):
        last = self.manifest['profiles'][-1]
        path = Path(last['config'])
        self.put(path, path.read_bytes().replace(b'/mcp/agents', b'/mcp/foreign'))
        last['sha256'] = profiles.digest(path.read_bytes())
        before = Path(self.manifest['policyFile']).read_bytes()
        with self.assertRaisesRegex(ValueError, 'not_opted_in'):
            self.prepare()
        self.assertEqual(Path(self.manifest['policyFile']).read_bytes(), before)
        self.assertFalse(Path(self.manifest['bindingsHost']).exists())
        self.assertFalse(self.hub.calls)
        disabled = Path(self.manifest['profiles'][0]['config']).read_bytes().replace(
            b'    url:', b"    enabled: 'false'\n    url:")
        self.put(path, disabled)
        last['sha256'] = profiles.digest(disabled)
        with self.assertRaisesRegex(ValueError, 'not_opted_in'):
            self.prepare()
        self.assertEqual(Path(self.manifest['policyFile']).read_bytes(), before)
        self.assertFalse(Path(self.manifest['bindingsHost']).exists())
        self.assertFalse(self.hub.calls)

    def test_duplicate_scope_bad_origins_and_runtime_drift_fail_closed(self):
        original = copy.deepcopy(self.manifest)
        for field, value in [('nativeUser', 'native-0'), ('config', self.manifest['profiles'][0]['config'])]:
            self.manifest = copy.deepcopy(original)
            self.manifest['profiles'][1][field] = value
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                self.prepare()
        self.manifest = copy.deepcopy(original); self.manifest['restBase'] = 'http://foreign:9081'
        with self.assertRaisesRegex(ValueError, 'origin'):
            self.prepare()
        self.manifest = copy.deepcopy(original)
        self.put(Path(self.manifest['runtimeHost']) / profiles.MODULES[0], b'changed')
        with self.assertRaisesRegex(ValueError, 'runtime_source_drift'):
            self.prepare()
        self.assertFalse(Path(self.manifest['bindingsHost']).exists())

    def test_policy_capacity_checked_for_all_profiles_without_staging(self):
        rows = [{'principal': 'p' + str(index), 'browserUser': 'u' + str(index),
                 'tokenSha256': profiles.digest(str(index).encode()), 'capabilities': ['browser']}
                for index in range(127)]
        policy = Path(self.manifest['policyFile'])
        self.put(policy, encode({'version': 1, 'clients': rows}))
        before = policy.read_bytes()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(policy.read_bytes(), before)
        self.assertFalse(Path(self.manifest['bindingsHost']).exists())

    def test_real_policy_reload_preserves_old_authority_and_separates_scratch_users(self):
        job, policy, template = self.prepare()
        spec = profiles.sys.modules['_browser_profile_policy']
        before = spec.Policy(self.manifest['policyFile']).authenticate('Bearer ' + 'a' * 64, 'browser')
        profiles.stage(self.manifest, job, policy, template, lambda: None)
        after = spec.Policy(self.manifest['policyFile']).authenticate('Bearer ' + 'a' * 64, 'browser')
        self.assertEqual(before, after)
        principals = []
        for row in self.manifest['profiles']:
            saved = job['profiles'][row['slug']]
            slot = Path(self.manifest['bindingsHost']) / saved['name']
            rest = json.loads((slot / 'rest.json').read_bytes())
            principal = spec.Policy(self.manifest['policyFile']).authenticate('Bearer ' + rest['token'], 'upload')
            principals.append(principal.browser_user)
            self.assertNotEqual(principal.browser_user, row['nativeUser'])
            native = json.loads((slot / 'native.json').read_bytes())
            self.assertEqual(native['profiles'], {'native': {'userId': row['nativeUser'], 'groupAccess': 'profile'}})
            self.assertEqual(native['managedUserId'], principal.browser_user)
            self.assertEqual(slot.stat().st_mode & 0o777, 0o700)
            self.assertEqual((slot / 'rest.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(set(principals)), 2)
        resumed, _, _ = profiles.preflight(self.manifest, self.hub, job)
        self.assertEqual(resumed, job)
        profiles.stage(self.manifest, job, policy, template, lambda: None)
        self.assertEqual(len(spec.Policy(self.manifest['policyFile']).load()), 3)

    def test_guarded_publication_mode_migration_rollback_and_retained_journal(self):
        job, policy, template = self.prepare()
        profiles.stage(self.manifest, job, policy, template, lambda: None)
        after = []
        for row in self.manifest['profiles']:
            saved = job['profiles'][row['slug']]
            saved['groupId'] = str(uuid.uuid4())
            key = {'id': str(uuid.uuid4()), 'token': 'mcphub_' + 'e' * 64}
            self.hub.rows.append({**key, 'name': saved['name'], 'kind': 'system', 'accessType': 'groups', 'enabled': False,
                                  'allowedGroups': [saved['groupId']], 'allowedServers': []})
            saved['key'] = key
            profiles.publish(self.manifest, self.hub, row, saved, key, lambda: None)
            path = Path(row['config']); after.append(path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn(b'# keep comment', after[-1]); self.assertIn(b'command: keep-me', after[-1])
        journal = Path(self.manifest['bindingsHost']) / job['profiles']['p0']['name'] / 'journal.json'
        self.put(journal, b'owned-pending-upload-marker')
        last = Path(self.manifest['profiles'][-1]['config'])
        self.put(last, after[-1] + b'foreign: preserve\n')
        with self.assertRaisesRegex(ValueError, 'rollback_client_drift'):
            profiles.rollback(self.manifest, self.hub, job, lambda: None)
        self.assertEqual(Path(self.manifest['profiles'][0]['config']).read_bytes(), after[0])
        self.assertTrue(all(row['enabled'] for row in self.hub.rows))
        self.put(last, after[-1]); last.chmod(0o640)
        with self.assertRaisesRegex(ValueError, 'client_metadata_drift'):
            profiles.rollback(self.manifest, self.hub, job, lambda: None)
        self.assertEqual(Path(self.manifest['profiles'][0]['config']).read_bytes(), after[0])
        last.chmod(0o600)
        self.hub.rows[-1]['name'] = 'foreign-renamed'
        with self.assertRaisesRegex(HubError, 'rollback_key_ownership_drift'):
            profiles.rollback(self.manifest, self.hub, job, lambda: None)
        self.assertEqual(Path(self.manifest['profiles'][0]['config']).read_bytes(), after[0])
        self.hub.rows[-1]['name'] = job['profiles']['p1']['name']
        profiles.rollback(self.manifest, self.hub, job, lambda: None)
        for row in self.manifest['profiles']:
            saved = job['profiles'][row['slug']]
            self.assertEqual(Path(row['config']).read_bytes(), base64.b64decode(saved['before']))
            self.assertEqual(Path(row['config']).stat().st_mode & 0o777, saved['mode'])
        self.assertTrue(all(not row['enabled'] for row in self.hub.rows))
        self.assertEqual(journal.read_bytes(), b'owned-pending-upload-marker')
        self.assertEqual(len(json.loads(Path(self.manifest['policyFile']).read_bytes())['clients']), 3)

    def test_client_edit_during_key_enable_is_not_overwritten(self):
        job, _, _ = self.prepare()
        row = self.manifest['profiles'][0]; saved = job['profiles'][row['slug']]
        saved['groupId'] = str(uuid.uuid4())
        key = {'id': str(uuid.uuid4()), 'token': 'mcphub_' + 'e' * 64}
        self.hub.rows.append({**key, 'name': saved['name'], 'kind': 'system', 'accessType': 'groups',
                             'enabled': False, 'allowedGroups': [saved['groupId']], 'allowedServers': []})
        original = self.hub.request
        path = Path(row['config']); foreign = path.read_bytes() + b'foreign: preserve\n'
        def edit_during_enable(method, uri, body=None):
            result = original(method, uri, body)
            self.put(path, foreign)
            return result
        self.hub.request = edit_during_enable
        with self.assertRaisesRegex(ValueError, 'client_publication_conflict'):
            profiles.publish(self.manifest, self.hub, row, saved, key, lambda: None)
        self.assertEqual(path.read_bytes(), foreign)
        self.assertTrue(self.hub.rows[0]['enabled'])  # retained unpublished key, not guessed rollback

    def test_policy_removed_after_preflight_is_refused_before_binding_writes(self):
        job, policy, template = self.prepare()
        path = Path(self.manifest['policyFile'])
        self.put(path, encode({'version': 1, 'clients': []}))
        with self.assertRaisesRegex(ValueError, 'policy_changed_after_preflight'):
            profiles.stage(self.manifest, job, policy, template, lambda: None)
        self.assertFalse(Path(self.manifest['bindingsHost']).exists())
        self.assertEqual(json.loads(path.read_bytes())['clients'], [])

    def test_legacy_identity_or_native_authority_requires_explicit_reconciliation(self):
        self.prepare()  # successful baseline before changing precisely this selector
        self.hub.value['systemConfig']['toolPolicies'][1]['keyId'] = 'another-key'
        with self.assertRaisesRegex(ValueError, 'identity_scoped_legacy_policy'):
            self.prepare()
        self.assertFalse(Path(self.manifest['bindingsHost']).exists())
        self.hub.value['systemConfig']['toolPolicies'][1].pop('keyId')
        self.hub.value['mcpServers']['legacy-browser']['env'] = {'SPARTAN_BROWSER_NATIVE_BINDING': '/private/legacy-native.json'}
        with self.assertRaisesRegex(ValueError, 'legacy_native_consumers'):
            self.prepare()
        self.assertFalse(Path(self.manifest['bindingsHost']).exists())

    def test_case_insensitive_authorization_is_replaced_not_duplicated(self):
        raw = Path(self.manifest['profiles'][0]['config']).read_bytes().replace(b'Authorization:', b'authorization:')
        changed = profiles.client_bytes(raw, 'http://caddy:18793/mcp/example', 'new-token')
        headers = profiles.object_bytes(changed, yaml=True)['mcp_servers'][NAME]['headers']
        self.assertEqual(dict(headers), {'authorization': 'Bearer new-token'})
        duplicate = raw.replace(b'authorization: Bearer shared-fixture-key',
                                b'authorization: Bearer shared-fixture-key\n      Authorization: Bearer duplicate')
        with self.assertRaisesRegex(ValueError, 'ambiguous_native_authorization'):
            profiles.client_bytes(duplicate, 'http://caddy:18793/mcp/example', 'new-token')

    def test_private_input_and_output_limits_and_template_digest_are_enforced(self):
        path = self.root / 'bounded.json'
        self.put(path, b'x' * profiles.MAX_PRIVATE)
        self.assertEqual(len(profiles.selected_bytes(path)), profiles.MAX_PRIVATE)
        self.put(path, b'x' * (profiles.MAX_PRIVATE + 1))
        with self.assertRaisesRegex(ValueError, 'unsafe_selected_file'):
            profiles.selected_bytes(path)
        with self.assertRaisesRegex(ValueError, 'private_write_too_large'):
            profiles.durable_write(self.root / 'not-created', b'x' * (profiles.MAX_PRIVATE + 1))
        self.assertFalse((self.root / 'not-created').exists())
        self.prepare()
        self.put(Path(self.manifest['nativeTemplate']), encode({'changed': 'synthetic'}))
        with self.assertRaisesRegex(ValueError, 'native_template_drift'):
            self.prepare()

    def test_hidden_native_upload_directory_is_valid_without_normalizing_traversal(self):
        path = Path(self.manifest['nativeTemplate'])
        template = profiles.read_json(str(path))
        for root in ('/root/.camofox/uploads/spartan-managed', '/root/.camofox/../outside',
                     '/root/./.camofox/uploads', '/root/.camofox/uploads/'):
            self.put(path, encode({**template, 'uploadRoot': root}))
            self.manifest['nativeTemplateSha256'] = profiles.digest(path.read_bytes())
            if root == '/root/.camofox/uploads/spartan-managed':
                self.assertEqual(profiles._template(self.manifest)['uploadRoot'], root)
            else:
                with self.assertRaisesRegex(ValueError, 'invalid_upload_root'):
                    profiles._template(self.manifest)

    def test_symlink_and_source_grant_drift_block_resume(self):
        job, _, _ = self.prepare()
        self.hub.value['groups'][0]['servers'][1]['tools'].append('new-common')
        with self.assertRaisesRegex(ValueError, 'source_grants_changed'):
            profiles.preflight(self.manifest, self.hub, job)
        path = Path(self.manifest['profiles'][0]['config'])
        target = path.with_suffix('.saved'); path.rename(target); path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'unsafe_selected_path'):
            self.prepare()


class ApiPeer(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_json(self, data):
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps({'success': True, 'data': data}).encode())

    def do_GET(self):
        rows = [{**row, 'token': 'masked'} for row in self.server.rows]
        self.send_json(rows)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        if self.path.endswith('/reset'):
            row = self.server.rows[0]; self.server.resets += 1
            row['token'] = 'mcphub_' + hashlib.sha256(str(self.server.resets).encode()).hexdigest()
            drop = self.server.drop_reset; self.server.drop_reset = False
        else:
            self.server.creates += 1
            row = {**body, 'id': 'c76414f4-7427-475e-8843-1e9f70abf6bf', 'token': 'mcphub_' + 'a' * 64}
            self.server.rows.append(row)
            drop = self.server.drop_create; self.server.drop_create = False
        if drop:
            self.close_connection = True
        else:
            self.send_json(row)


class KeyRecovery(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ApiPeer)
        self.server.rows = []; self.server.creates = self.server.resets = 0
        self.server.drop_create = self.server.drop_reset = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.hub = Hub('http://127.0.0.1:' + str(self.server.server_port), 'synthetic-admin-token')
        self.saved = {}

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def persist(self, value):
        self.saved.clear(); self.saved.update(value)

    def test_lost_create_and_reset_reconcile_same_disabled_key_without_duplicate_creation(self):
        self.server.drop_create = True
        with self.assertRaises(HubError):
            self.hub.prepare_key('fixture', 'group', self.saved, self.persist)
        self.assertEqual(self.saved['phase'], 'creating')
        self.server.drop_reset = True
        with self.assertRaises(HubError):
            self.hub.prepare_key('fixture', 'group', self.saved, self.persist)
        self.assertEqual(self.saved['phase'], 'rotating')
        result = self.hub.prepare_key('fixture', 'group', self.saved, self.persist)
        self.assertEqual((self.server.creates, self.server.resets), (1, 2))
        self.assertFalse(self.server.rows[0]['enabled'])
        self.assertEqual(result['token'], self.server.rows[0]['token'])
        self.assertEqual(result['id'], self.server.rows[0]['id'])
        self.assertEqual(self.hub.prepare_key('fixture', 'group', self.saved, self.persist), result)
        self.assertEqual(self.server.resets, 2)

    def test_secret_save_failure_retains_disabled_owned_key_for_supported_reset(self):
        def fail_secret(value):
            if 'token' in value:
                raise OSError('synthetic write refusal')
            self.persist(value)
        with self.assertRaises(OSError):
            self.hub.prepare_key('fixture', 'group', self.saved, fail_secret)
        self.assertFalse(self.server.rows[0]['enabled'])
        result = self.hub.prepare_key('fixture', 'group', self.saved, self.persist)
        self.assertTrue(result['token'].startswith('mcphub_'))
        self.assertEqual((self.server.creates, self.server.resets), (1, 1))

    def test_foreign_or_published_or_drifted_key_is_not_reset(self):
        key = self.hub.prepare_key('fixture', 'group', self.saved, self.persist)
        for saved in ({}, {**self.saved, 'group': 'other'}):
            with self.assertRaises(HubError):
                self.hub.prepare_key('fixture', 'group', saved, self.persist)
        self.saved.pop('token'); self.server.rows[0]['enabled'] = True
        with self.assertRaisesRegex(HubError, 'published'):
            self.hub.prepare_key('fixture', 'group', self.saved, self.persist)
        self.assertEqual(self.server.resets, 0)
        self.assertEqual(self.server.rows[0]['id'], key['id'])


if __name__ == '__main__':
    unittest.main()
