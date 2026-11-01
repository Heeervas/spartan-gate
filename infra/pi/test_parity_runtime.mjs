import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { copyFile, mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { piDir } from './pi_test_inputs.mjs';

const exec = promisify(execFile);
test('pinned native loader and detached Route child own replay and continuation without mutating parent', { timeout: 120000 }, async () => {
  const root = await mkdtemp('/tmp/pi-parity-');
  try {
    await exec('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', join(root, 'key.pem'),
      '-out', join(root, 'cert.pem'), '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1']);
    const env = { PATH: process.env.PATH, HOME: root, XDG_DATA_HOME: join(root, 'data'), XDG_CONFIG_HOME: join(root, 'config'),
      PI_OFFLINE: '1', PI_SKIP_VERSION_CHECK: '1', PI_TELEMETRY: '0', PI_TEST_PACKAGE_DIR: piDir,
      PI_CODEX_FAST_SOURCE: process.env.PI_CODEX_FAST_SOURCE,
      ...(process.env.PI_PARITY_ROUTE_SOURCE ? { PI_PARITY_ROUTE_SOURCE: process.env.PI_PARITY_ROUTE_SOURCE } : {}),
      ...(process.env.PI_PARITY_ROLE_ROOT ? { PI_PARITY_ROLE_ROOT: process.env.PI_PARITY_ROLE_ROOT } : {}),
      PI_SUBAGENTS_PACKAGE_ROOT: process.env.PI_SUBAGENTS_PACKAGE_ROOT || join(process.env.HOME, '.local/share/pi-subagents-nicobailon'),
      PI_SUBAGENTS_PI_CODING_AGENT_PACKAGE_ROOT: piDir, NODE_EXTRA_CA_CERTS: join(root, 'cert.pem'), SSL_CERT_FILE: join(root, 'cert.pem') };
    const { stdout, stderr } = await exec(process.execPath, ['infra/pi/parity_runtime_fixture.mjs', root], { env, timeout: 110000, maxBuffer: 1024 * 1024 });
    assert.match(stdout, /PARITY_NATIVE_OK/);
    assert.doesNotMatch(stderr, /Extension error|failed to load/i);
    console.log(stdout.trim());
    const negativeRoot = await mkdtemp(join(root, 'negative-'));
    for (const name of ['key.pem', 'cert.pem']) await copyFile(join(root, name), join(negativeRoot, name));
    const negative = await exec(process.execPath, ['infra/pi/parity_runtime_fixture.mjs', negativeRoot, 'missing-child-provider'], {
      env: { ...env, HOME: negativeRoot, XDG_DATA_HOME: join(negativeRoot, 'data'), XDG_CONFIG_HOME: join(negativeRoot, 'config') },
      timeout: 110000, maxBuffer: 1024 * 1024,
    });
    assert.match(negative.stdout, /PARITY_NEGATIVE_OK/);
    console.log(negative.stdout.trim());
    const fastRoot = await mkdtemp(join(root, 'fast-'));
    for (const name of ['key.pem', 'cert.pem']) await copyFile(join(root, name), join(fastRoot, name));
    const fast = await exec(process.execPath, ['infra/pi/parity_runtime_fixture.mjs', fastRoot, 'fast-inheritance'], {
      env: { ...env, HOME: fastRoot, XDG_DATA_HOME: join(fastRoot, 'data'), XDG_CONFIG_HOME: join(fastRoot, 'config') },
      timeout: 110000, maxBuffer: 1024 * 1024,
    });
    assert.match(fast.stdout, /PARITY_FAST_OK/);
    console.log(fast.stdout.trim());
  } finally { await rm(root, { recursive: true, force: true }); }
});
