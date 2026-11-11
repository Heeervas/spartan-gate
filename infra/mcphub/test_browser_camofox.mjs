import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, writeFile, chmod, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';
import { NativeBrowser, loadNativeBinding, validateNativeBinding } from './browser-camofox.mjs';
const binding = baseUrl => ({ baseUrl, token: 'native-key-1234567890', managedUserId: 'managed',
  profiles: { main: { userId: 'native', sessionKeys: ['group'] } }, uploadRoot: '/browser/uploads' });

test('native binding is private, exact-schema, fixed-origin and never silently falls back', async () => {
  const root = await mkdtemp(join(tmpdir(), 'native-binding-')); await chmod(root, 0o700);
  const path = join(root, 'binding.json'); await writeFile(path, JSON.stringify(binding('http://camofox:9377')), { mode: 0o600 });
  try {
    assert.equal((await loadNativeBinding(path)).profiles.main.userId, 'native');
    await chmod(path, 0o644); await assert.rejects(loadNativeBinding(path)); await chmod(path, 0o600);
    const link = join(root, 'link'); await symlink(path, link); await assert.rejects(loadNativeBinding(link));
    await chmod(root, 0o755); await assert.rejects(loadNativeBinding(path)); await chmod(root, 0o700);
    for (const baseUrl of ['https://camofox:9377', 'http://example.com', 'http://camofox:9377/other', 'http://user@camofox:9377']) {
      assert.throws(() => validateNativeBinding(binding(baseUrl)));
    }
    assert.throws(() => validateNativeBinding({ ...binding('http://camofox:9377'), headers: {} }));
    assert.throws(() => validateNativeBinding({ ...binding('http://camofox:9377'), uploadRoot: '/browser/../secret' }));
    assert.throws(() => validateNativeBinding({ ...binding('http://camofox:9377'), profiles: { main: { userId: 'native', sessionKeys: [] } } }));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('profile-wide groups require one fixed identity and explicit opt-in; legacy lists remain strict', () => {
  const base = binding('http://camofox:9377');
  const wide = { userId: 'native', groupAccess: 'profile' };
  assert.deepEqual(validateNativeBinding({ ...base, profiles: { native: wide } }).profiles.native, wide);
  for (const profiles of [
    { native: { ...wide, sessionKeys: ['group'] } },
    { native: { ...wide, groupAccess: 'all-users' } },
    { native: wide, other: { userId: 'other', sessionKeys: ['group'] } },
  ]) assert.throws(() => validateNativeBinding({ ...base, profiles }));
  assert.deepEqual(validateNativeBinding(base).profiles.main.sessionKeys, ['group']);
});

test('native transport rejects redirects, encoded/oversized responses, and arbitrary route/admin calls', async () => {
  let mode = 'redirect'; let calls = 0;
  const server = createServer((req, res) => {
    calls++; assert.equal(req.headers.authorization, 'Bearer native-key-1234567890');
    if (mode === 'redirect') { res.writeHead(302, { Location: 'http://example.com' }); res.end(); }
    if (mode === 'encoded') { res.writeHead(200, { 'Content-Encoding': 'gzip' }); res.end('no'); }
    if (mode === 'large' || mode === 'large_error') { res.writeHead(mode === 'large' ? 200 : 500); res.write('x'.repeat(70000)); res.end('x'.repeat(70000)); }
    if (mode === 'ok') res.end('{"ok":true,"result":7}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const native = new NativeBrowser(binding(`http://127.0.0.1:${server.address().port}`));
  try {
    await assert.rejects(native.request('GET', '/stop', 'native'), { message: 'invalid_request' }); assert.equal(calls, 0);
    for (mode of ['redirect', 'encoded', 'large']) await assert.rejects(native.request('GET', '/tabs', 'native'));
    mode = 'ok'; assert.equal(await native.evaluate('native', 't', '7'), 7);
    mode = 'redirect'; await assert.rejects(native.evaluate('native', 't', '7'), { message: 'remote_outcome_unknown', ambiguous: true });
    mode = 'encoded'; await assert.rejects(native.evaluate('native', 't', '7'), { message: 'remote_outcome_unknown', ambiguous: true });
    mode = 'large'; await assert.rejects(native.evaluate('native', 't', '7'), { message: 'response_too_large', ambiguous: false });
    mode = 'large_error'; await assert.rejects(native.evaluate('native', 't', '7'), { message: 'remote_outcome_unknown', ambiguous: true });
    assert.equal(calls, 8);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});
