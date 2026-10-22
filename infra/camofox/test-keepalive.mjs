// Only the native patch-chain subprocess lacks a live server handle. Do not
// load node:test for ordinary JSON-producing Node fixtures (it emits TAP).
if (process.argv.some(argument => argument.endsWith('/test_patch_chain_upstream.js'))) {
  const { beforeEach, afterEach } = await import('node:test');
  let keepalive;
  beforeEach(() => { keepalive = setTimeout(() => {}, 30_000); });
  afterEach(() => { clearTimeout(keepalive); });
}
