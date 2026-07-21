const assert = require('assert');
const test = require('node:test');

const { patchLogRedaction } = require('./patch-log-redaction.js');

function fixture() {
  return `// --- Structured logging ---
function log(level, msg, fields = {}) {
  const entry = {
    ts: new Date().toISOString(),
    level,
    msg,
    ...fields,
  };
  const line = JSON.stringify(entry);
  if (level === 'error') {
    process.stderr.write(line + '\\n');
  } else {
    process.stdout.write(line + '\\n');
  }
}

function sentry(res) {
  return {
      path: res.req?.originalUrl,
  };
}
`;
}

test('structured logs and Sentry paths remove URL query credentials', () => {
  const patched = patchLogRedaction(fixture());
  assert.strictEqual(patchLogRedaction(patched), patched, 'patch must be idempotent');
  assert.match(patched, /SPARTAN_LOG_URL_REDACTION_V1/);
  assert.match(patched, /path: res\.req\?\.path/);
  assert.doesNotMatch(patched, /path: res\.req\?\.originalUrl/);

  const lines = [];
  const fakeProcess = {
    stdout: { write(line) { lines.push(line); } },
    stderr: { write(line) { lines.push(line); } },
  };
  const runtime = Function('process', `${patched}; return { log };`)(fakeProcess);
  runtime.log('info', 'navigated', {
    url: 'http://localhost:8976/oauth/callback?code=oauth-secret&state=state-secret#fragment',
    path: '/internal/checkpoint?token=private',
    error: 'failed at https://example.test/callback?code=error-secret',
  });

  const entry = JSON.parse(lines[0]);
  assert.strictEqual(entry.url, 'http://localhost:8976/oauth/callback');
  assert.strictEqual(entry.path, '/internal/checkpoint');
  assert.strictEqual(entry.error, 'failed at https://example.test/callback');
  assert.doesNotMatch(lines[0], /oauth-secret|state-secret|private|error-secret|fragment/);
});
