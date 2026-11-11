import assert from 'node:assert/strict';
import { mkdtemp, chmod, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import test from 'node:test';
import { BrowserState } from './browser-state.mjs';

async function fixture(run) {
  const dir = await mkdtemp(join(tmpdir(), 'browser-state-'));
  await chmod(dir, 0o700);
  const path = join(dir, 'state.json');
  let state = await BrowserState.open(path, 'fixture-realm');
  try { await run({ state, path, reopen: async () => { await state.close(); state = await BrowserState.open(path, 'fixture-realm'); return state; } }); }
  finally { await state.close(); await rm(dir, { recursive: true, force: true }); }
}

test('concurrent identical creates reserve exactly one session and survive reopen', () => fixture(async f => {
  const id = randomUUID();
  const outcomes = await Promise.all(Array.from({ length: 4 }, () => f.state.begin(id, 'fingerprint', undefined, true)));
  assert.equal(outcomes.filter(value => value.fresh).length, 1);
  const session = outcomes.find(value => value.fresh).session_id;
  await f.state.complete(id, { content: [{ type: 'text', text: JSON.stringify({ session_id: session }) }] }, { workflow: 'b'.repeat(64), status: 'active' });
  const state = await f.reopen();
  const duplicate = await state.begin(id, 'fingerprint', undefined, true);
  assert.equal(duplicate.result.content[0].text, JSON.stringify({ session_id: session }));
  assert.equal(state.session(session).workflow, 'b'.repeat(64));
  await assert.rejects(state.begin(id, 'different', undefined, true), /operation_conflict/);
}));

test('pending operation is never redispatched and unknown resources never allocate', () => fixture(async f => {
  const id = randomUUID();
  const first = await f.state.begin(id, 'same', undefined, true);
  const state = await f.reopen();
  assert.equal((await state.begin(id, 'same', undefined, true)).pending, true);
  await assert.rejects(state.begin(randomUUID(), 'unknown', randomUUID()), /stale_session/);
  assert.equal(state.session(first.session_id).status, 'minting');
  const disk = JSON.parse(await readFile(f.path, 'utf8'));
  assert.equal(Object.keys(disk.operations).length, 1);
}));

test('completed cleanup replay is available after the session is closed', () => fixture(async f => {
  const create = randomUUID(); const session = (await f.state.begin(create, 'create', undefined, true)).session_id;
  await f.state.complete(create, { content: [] }, { workflow: 'a'.repeat(64), status: 'active' });
  const cleanup = randomUUID();
  await f.state.begin(cleanup, 'cleanup', session);
  const result = { content: [{ type: 'text', text: '{"ok":true}' }] };
  await f.state.complete(cleanup, result, { status: 'closed', workflow: null });
  assert.deepEqual((await f.state.begin(cleanup, 'cleanup', session)).result, result);
  await assert.rejects(f.state.begin(randomUUID(), 'new', session), /stale_session/);
}));

test('single writer, safe file mode and realm binding are enforced', () => fixture(async f => {
  await assert.rejects(BrowserState.open(f.path, 'fixture-realm'), /state_locked/);
  await f.state.close();
  await assert.rejects(BrowserState.open(f.path, 'foreign-realm'), /state_unavailable/);
  await chmod(f.path, 0o644);
  await assert.rejects(BrowserState.open(f.path, 'fixture-realm'), /state_unavailable/);
}));

test('corrupt state is refused rather than silently reset', () => fixture(async f => {
  await f.state.close(); await writeFile(f.path, '{', { mode: 0o600 });
  await assert.rejects(BrowserState.open(f.path, 'fixture-realm'), /state_unavailable/);
  assert.equal(await readFile(f.path, 'utf8'), '{');
}));

test('native attachment upgrades state format and refuses downgraded authority instead of silently losing guards', () => fixture(async f => {
  const create = randomUUID(); const id = (await f.state.begin(create, 'create', undefined, true)).session_id;
  await f.state.complete(create, { content: [] }, { workflow: 'a'.repeat(64), status: 'active' });
  assert.equal(JSON.parse(await readFile(f.path, 'utf8')).version, 1, 'old REST journals remain version 1');
  const attach = randomUUID(); await f.state.begin(attach, 'attach', id);
  await f.state.complete(attach, { content: [] }, { attachment: { profile: 'main', group: 'group', tab_id: 'native', realm: 'b'.repeat(64) } });
  const state = await f.reopen(); assert.equal(state.session(id).attachment.tab_id, 'native');
  const disk = JSON.parse(await readFile(f.path, 'utf8')); assert.equal(disk.version, 2);
  await state.close(); disk.version = 1; await writeFile(f.path, JSON.stringify(disk));
  await assert.rejects(BrowserState.open(f.path, 'fixture-realm'), /state_unavailable/);
}));

test('session capacity and mutation-result reservation stop before excess allocation', () => fixture(async f => {
  for (let index = 0; index < 8; index++) await f.state.begin(randomUUID(), 'create-' + index, undefined, true);
  await assert.rejects(f.state.begin(randomUUID(), 'ninth', undefined, true), /capacity/);
  const id = Object.keys(JSON.parse(await readFile(f.path, 'utf8')).operations)[0];
  await assert.rejects(f.state.complete(id, { content: [{ type: 'text', text: 'x'.repeat(4096) }] }), /state_unavailable/);
}));
