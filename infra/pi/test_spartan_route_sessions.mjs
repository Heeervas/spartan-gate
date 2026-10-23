import assert from 'node:assert/strict';
import test from 'node:test';
import { routeSessionId } from './extensions/spartan-route-usage/session.mjs';

test('existing UUID identities remain unchanged, including uppercase normalization', () => {
  const uuid = 'a7becb62-8311-466d-89ca-e8b215d0a700';
  assert.equal(routeSessionId(uuid), uuid);
  assert.equal(routeSessionId(uuid.toUpperCase()), uuid);
});

test('named Pi IDs have a pinned, case-sensitive UUIDv8 identity across restarts', () => {
  const named = 'subagent.0123456789abcdef';
  assert.equal(routeSessionId(named), '3bab243f-a0c3-8494-9c22-072bbf68d2f7');
  assert.notEqual(routeSessionId(named), routeSessionId('subagent.0123456789abcdee'));
  assert.notEqual(routeSessionId(named), routeSessionId(named.toUpperCase()));
  for (const value of ['a', 'native-session_1', 'x'.repeat(256)]) {
    assert.match(routeSessionId(value), /^[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  }
});

test('invalid and unbounded identities cannot obtain a Route session', () => {
  for (const value of [undefined, null, 1, {}, '', ' ', ' a', 'a ', '../a', 'a/b', 'a:b', 'a\nb', 'a\0b', '-a', 'a_', 'a.', 'x'.repeat(257)]) {
    assert.equal(routeSessionId(value), undefined);
  }
});
