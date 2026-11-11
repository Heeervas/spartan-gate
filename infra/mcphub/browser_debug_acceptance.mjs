/** Optional real Camoufox page-producer check. Existing image, network disabled,
 * no profiles/mounts/credentials/sites. Not native-Hermes or deployment proof.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { debugScript, waitScript } from './browser-debug.mjs';
const require = createRequire('/app/package.json');
const { Camoufox } = await import(pathToFileURL(require.resolve('camoufox-js')));
const deadline = setTimeout(() => { console.error('fixture_deadline'); process.exit(1); }, 45000); deadline.unref();
console.log('fixture_launch_virtual_display');
const browser = await Camoufox({ headless: 'virtual', geoip: false });
console.log('fixture_browser_ready');
try {
  const page = await browser.newPage();
  console.log('fixture_page_ready');
  let received = 0;
  await page.route('https://fixture.invalid/**', async route => {
    if (route.request().method() === 'PUT') {
      assert.equal(route.request().postData(), 'SYNTHETIC-ONLY'); received++;
      await route.fulfill({ status: 403, body: 'synthetic refusal' }); return;
    }
    await route.fulfill({ status: 200, contentType: 'text/html', body: `<!doctype html><input type="file" id="cv"><script>
      window.fixtureFetch = fetch;
      document.querySelector('#cv').onchange = async event => {
        const response = await fetch('/upload?token=SYNTHETIC_TOKEN', { method: 'PUT', body: event.target.files[0] });
        console.warn('synthetic upload status', response.status); window.fixtureDone = true;
      };
    </script>` });
  });
  await page.goto('https://fixture.invalid/form', { timeout: 10000 });
  console.log('fixture_form_ready');
  assert.deepEqual(await page.evaluate(waitScript('#cv', 1000)), { found: true });
  assert.deepEqual(await page.evaluate(waitScript('#absent', 10)), { found: false });
  const capture_id = 'synthetic-capture';
  assert.equal((await page.evaluate(debugScript('start', { capture_id, ttl_ms: 1000 }))).ok, true);
  await page.locator('#cv').setInputFiles({ name: 'fixture.txt', mimeType: 'text/plain', buffer: Buffer.from('SYNTHETIC-ONLY') });
  await page.waitForFunction(() => window.fixtureDone === true);
  const result = await page.evaluate(debugScript('read', { capture_id, cursor: 0, limit: 20 }));
  assert.equal(result.ok, true); assert.equal(received, 1);
  assert.ok(result.events.some(event => event.kind === 'fetch_end' && event.status === 403));
  assert.ok(result.events.some(event => event.kind === 'console' && event.text.includes('403')));
  assert.ok(!JSON.stringify(result).includes('SYNTHETIC_TOKEN')); assert.ok(!JSON.stringify(result).includes('SYNTHETIC-ONLY'));
  await page.evaluate(debugScript('stop', { capture_id }));
  assert.equal(await page.evaluate(() => fetch === window.fixtureFetch), true);
  await page.evaluate(debugScript('start', { capture_id, ttl_ms: 1000 }));
  await page.waitForFunction(() => window.__spartan_mcp_debug_v1__.active === false);
  assert.equal(await page.evaluate(() => fetch === window.fixtureFetch), true);
  await page.evaluate(debugScript('stop', { capture_id }));
  console.log(JSON.stringify({ result: 'PASS', boundary: 'real-Camoufox-page-producer', upload_put_status: 403,
    console_capture: true, source_body_not_recorded: true, explicit_stop_and_expiry_restore: true,
    network: 'disabled; synthetic requests fulfilled by fixture', native_Hermes_or_deployment: false }));
} finally { await browser.close(); clearTimeout(deadline); }
