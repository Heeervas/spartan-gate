import assert from 'node:assert/strict';
import test from 'node:test';
import { BrowserHTTP, BrowserError } from './browser-http.mjs';

// Inert exchange failures isolate diagnostic mapping, not actual timer latency.
// Real HTTP bounds and lifecycle are exercised by the SDK/REST fixtures.
test('transport preserves recognized read deadlines without making mutations replayable', async t => {
  const binding = { baseUrl: 'http://127.0.0.1:12345', host: 'tool-edge:18792', token: 'a'.repeat(64), statePath: '/unused' };
  for (const [mutation, aborted, timeout, expected] of [
    [false, false, true, 'deadline'], [true, false, true, 'remote_outcome_unknown'],
    [false, true, true, 'cancelled'], [false, false, false, 'upstream_error'],
  ]) {
    await t.test(expected, async () => {
      const transport = new BrowserHTTP(binding);
      transport.exchange = async () => { throw timeout ? new BrowserError('deadline') : new Error('private upstream detail'); };
      await assert.rejects(transport.call('POST', '/v1/browser/snapshot', {}, undefined, { aborted }, { mutation }), error => {
        assert.equal(error.message, expected); assert.equal(error.ambiguous, mutation); return true;
      });
    });
  }
});
