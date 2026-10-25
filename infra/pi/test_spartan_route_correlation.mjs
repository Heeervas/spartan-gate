import assert from 'node:assert/strict';
import test from 'node:test';
import { correlationHeaders } from './extensions/spartan-route-usage/correlation.mjs';

test('native labels use the stable session without a registration RPC and preserve other headers', () => {
  const headers = { Authorization: 'secret', 'X-Spartan-Pi-Client': '1',
    'X-Spartan-Pi-Origin': 'origin', 'X-Spartan-Pi-Session': 'usage-session' };
  const before = { ...headers };
  correlationHeaders(headers, { sessionManager: { getSessionId: () => 'subagent.0123456789abcdef' } });
  assert.deepEqual(JSON.parse(headers['X-Spartan-Correlation']), { version: 1, client: 'pi',
    conversation: 'subagent.0123456789abcdef', purpose: 'other' });
  delete headers['X-Spartan-Correlation'];
  assert.deepEqual(headers, before);
});

test('unmarked transports are untouched and case variants cannot create duplicate correlation headers', () => {
  const headers = { Authorization: 'other' };
  correlationHeaders(headers, {});
  assert.deepEqual(headers, { Authorization: 'other' });
  const marked = { 'x-spartan-pi-client': '1', 'x-spartan-correlation': 'stale' };
  correlationHeaders(marked, { sessionManager: { getSessionId: () => 'stable' } });
  assert.equal(Object.keys(marked).filter(key => key.toLowerCase() === 'x-spartan-correlation').length, 1);
  assert.equal(JSON.parse(marked['X-Spartan-Correlation']).conversation, 'stable');
});

test('invalid native identifiers do not manufacture a grouping identity', () => {
  for (const id of ['', 'private/path', 'é', 'valid\n', 'x'.repeat(129), null]) {
    const headers = { 'X-Spartan-Pi-Client': '1' };
    correlationHeaders(headers, { sessionManager: { getSessionId: () => id } });
    assert.equal(headers['X-Spartan-Correlation'], undefined);
  }
});
