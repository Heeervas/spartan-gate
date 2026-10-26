import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const script = resolve('scripts/configure-shared-tools.mjs');
const token = 'ab'.repeat(32); // Synthetic only, never reads the active agent home.
function fixture(t) {
  const root = fs.mkdtempSync(join(tmpdir(), 'gate-installer-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const agent = join(root, 'agent');
  fs.mkdirSync(agent, { mode: 0o700 });
  const credential = join(root, 'credential');
  fs.writeFileSync(credential, `${token}\n`, { mode: 0o600 });
  const state = join(root, 'state');
  const backup = join(root, 'backup');
  const webPath = join(agent, 'web-search.json');
  const original = { searchRouting: { providers: ['tavily'], fallbackOn: ['network'] }, foreign: { keep: 'yes' }, commands: { custom: { enabled: true }, search: { foreign: 42 } }, tools: { sourceCheck: { foreign: true } }, webSearch: { other: 17 } };
  fs.writeFileSync(webPath, JSON.stringify(original), { mode: 0o600 });
  const args = ['--agent-dir', agent, '--base-url', 'http://127.0.0.1:18792', '--client-id', 'fixture-client', '--credential-file', credential, '--state-dir', state];
  const env = { ...process.env, PI_CODING_AGENT_DIR: agent };
  delete env.SEARXNG_BASE_URL;
  const run = (extra = [], environment = env) => spawnSync(process.execPath, [script, ...args, ...extra], { env: environment, encoding: 'utf8' });
  return { root, agent, credential, state, backup, webPath, original, args, env, run };
}
function snapshot(root) {
  const values = {};
  function visit(path) {
    const stat = fs.lstatSync(path);
    values[path.slice(root.length)] = stat.isDirectory() ? ['directory', stat.mode & 0o777] : [stat.mode & 0o777, fs.readFileSync(path).toString('hex')];
    if (stat.isDirectory()) for (const child of fs.readdirSync(path)) visit(join(path, child));
  }
  visit(root);
  return values;
}
const mode = path => fs.statSync(path).mode & 0o777;


test('default CLI preview makes zero changes and prints no configuration or bearer', t => {
  const f = fixture(t);
  const before = snapshot(f.root);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr + result.stdout);
  assert.match(result.stdout, /DRYRUN/);
  assert.match(result.stdout, /change .*web-search.json/);
  assert.ok(!result.stdout.includes(token));
  assert.ok(!result.stdout.includes('Bearer'));
  assert.ok(!result.stdout.includes('"foreign"'));
  assert.deepEqual(snapshot(f.root), before);
});

test('invalid later JSON and symlink targets fail complete preflight without writes', t => {
  const f = fixture(t);
  fs.writeFileSync(f.webPath, `{"secret":"${token}",`);
  let before = snapshot(f.root);
  let result = f.run(['--apply', '--backup-dir', f.backup]);
  assert.equal(result.status, 1);
  assert.ok(!result.stdout.includes(token));
  assert.deepEqual(snapshot(f.root), before);
  fs.writeFileSync(f.webPath, JSON.stringify(f.original));
  fs.symlinkSync(f.root, join(f.agent, 'extensions'));
  before = fs.readFileSync(f.webPath);
  result = f.run(['--apply', '--backup-dir', f.backup]);
  assert.equal(result.status, 1);
  assert.deepEqual(fs.readFileSync(f.webPath), before);
  assert.equal(fs.existsSync(f.backup), false);
  assert.equal(fs.existsSync(f.state), false);
});

test('apply creates strict configuration, preserves foreign fields, protects backups and is idempotent', t => {
  const f = fixture(t);
  const before = snapshot(f.root);
  assert.equal(f.run(['--apply']).status, 1, 'overwrite needs explicit backup directory');
  assert.deepEqual(snapshot(f.root), before);
  const result = f.run(['--apply', '--backup-dir', f.backup]);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.ok(!result.stdout.includes(token));
  const configPath = join(f.agent, 'spartan-tools.json');
  assert.deepEqual(JSON.parse(fs.readFileSync(configPath)), { version: 1, baseUrl: 'http://127.0.0.1:18792', credentialFile: f.credential, stateDir: f.state, clientId: 'fixture-client' });
  const web = JSON.parse(fs.readFileSync(f.webPath));
  assert.deepEqual(web.foreign, f.original.foreign);
  assert.deepEqual(web.commands.custom, { enabled: true });
  assert.equal(web.commands.search.foreign, 42);
  assert.equal(web.tools.sourceCheck.foreign, true);
  assert.equal(web.webSearch.other, 17);
  assert.equal(web.searchProvider, 'searxng');
  assert.equal(web.provider, 'searxng');
  assert.equal(web.workflow, 'none');
  assert.deepEqual(web.webSearch.allowedProviders, ['searxng']);
  assert.equal(Object.hasOwn(web, 'searchRouting'), false);
  assert.deepEqual(web.searxngHeaders, { Authorization: `Bearer ${token}` });
  for (const command of ['websearch', 'curator', 'search', 'google-account']) assert.equal(web.commands[command].enabled, false);
  for (const tool of ['sourceCheck', 'fetchContent']) assert.equal(web.tools[tool].enabled, false);
  assert.equal(mode(f.state), 0o700);
  assert.equal(mode(f.backup), 0o700);
  assert.equal(mode(configPath), 0o600);
  assert.equal(mode(f.webPath), 0o600);
  const backups = fs.readdirSync(f.backup).map(name => join(f.backup, name));
  assert.equal(backups.length, 1);
  assert.deepEqual(JSON.parse(fs.readFileSync(backups[0])), f.original);
  assert.equal(mode(backups[0]), 0o600);
  assert.equal(fs.statSync(backups[0]).uid, process.getuid());
  for (const name of ['spartan-tools.ts', 'spartan-tools/client.mjs', 'spartan-tools/config.mjs', 'spartan-tools/http.mjs', 'spartan-tools/state.mjs']) {
    const path = join(f.agent, 'extensions', name);
    assert.equal(mode(path), 0o644);
    assert.deepEqual(fs.readFileSync(path), fs.readFileSync(join('infra/pi/extensions', name)));
  }
  const installed = snapshot(f.root);
  const repeat = f.run(['--apply']);
  assert.equal(repeat.status, 0, repeat.stdout);
  assert.doesNotMatch(repeat.stdout, /^change /m);
  assert.deepEqual(snapshot(f.root), installed);
});

test('owner-only enclosure admits same-owner group-writable ancestors but not exposed or world-writable paths', t => {
  const f = fixture(t);
  const shared = join(f.root, 'group-writable');
  fs.mkdirSync(shared);
  fs.chmodSync(shared, 0o775);
  const credential = join(shared, 'credential');
  fs.renameSync(f.credential, credential);
  f.args[f.args.indexOf('--credential-file') + 1] = credential;
  let before = snapshot(f.root);
  let result = f.run();
  assert.equal(result.status, 0, 'same-owner 0775 below verified 0700 must preview safely');
  assert.deepEqual(snapshot(f.root), before);
  fs.chmodSync(f.root, 0o755);
  before = snapshot(f.root);
  result = f.run();
  assert.equal(result.status, 1, 'exposed group-writable ancestor must fail closed');
  assert.deepEqual(snapshot(f.root), before);
  fs.chmodSync(f.root, 0o700);
  fs.chmodSync(shared, 0o777);
  before = snapshot(f.root);
  result = f.run();
  assert.equal(result.status, 1, 'private enclosure must not admit world-writable descendants');
  assert.deepEqual(snapshot(f.root), before);
  fs.chmodSync(shared, 0o775);
  const preload = join(f.root, 'foreign-owner.mjs');
  // Metadata fault fixture: unprivileged tests cannot chown a real directory.
  fs.writeFileSync(preload, `import fs from 'node:fs'; const original = fs.lstatSync; fs.lstatSync = (path, ...args) => { const value = original(path, ...args); if (path === ${JSON.stringify(shared)}) value.uid += 1; return value; };`);
  before = snapshot(f.root);
  result = f.run([], { ...f.env, NODE_OPTIONS: `--import=${preload}` });
  assert.equal(result.status, 1, 'private enclosure must not admit foreign-owner writable metadata');
  assert.deepEqual(snapshot(f.root), before);
});

test('endpoint override mismatch and credential permissions deny without exposing values or changing targets', t => {
  const f = fixture(t);
  let before = snapshot(f.root);
  let result = f.run(['--apply', '--backup-dir', f.backup], { ...f.env, SEARXNG_BASE_URL: `https://example.invalid/${token}` });
  assert.equal(result.status, 1);
  assert.ok(!result.stdout.includes(token));
  assert.deepEqual(snapshot(f.root), before);
  fs.chmodSync(f.credential, 0o644);
  before = snapshot(f.root);
  result = f.run(['--apply', '--backup-dir', f.backup]);
  assert.equal(result.status, 1);
  assert.deepEqual(snapshot(f.root), before);
});
