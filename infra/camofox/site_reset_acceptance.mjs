/** Opt-in G1 proof, not a service or route implementation.
 * Run in the existing Camofox image, --network none, with the fixture mounted RO.
 * Required --add-host names: target.parent.test, child.target.parent.test,
 * sibling.parent.test, parent.test, other.test (all 127.0.0.1).
 * No interception is used by the cookie or cache-control origin servers.
 * Only P2 installs routes, each of which falls through to the real server.
 * Importing this file starts nothing; the selector is reusable by unit tests.
 */
import assert from 'node:assert/strict';
import { randomBytes, createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer as httpServer } from 'node:http';
import { createServer as httpsServer } from 'node:https';
import { createRequire } from 'node:module';
import { domainMatches } from './patch-site-reset.js';
export { domainMatches } from './patch-site-reset.js';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Diseño v2.3, deliberately retaining the Firefox adapter's actual limitations.
// No clearCookies, URL-filtered inventory, partition guessing or automatic retry.
async function expireSelected(context, host) {
  const before = await context.cookies();
  const selected = before.filter((cookie) => domainMatches(cookie.domain, host));
  let writeError = false;
  for (const cookie of selected) {
    try {
      await context.addCookies([
        {
          name: cookie.name,
          value: '',
          domain: cookie.domain,
          path: cookie.path,
          secure: cookie.secure,
          httpOnly: cookie.httpOnly,
          sameSite: cookie.sameSite,
          expires: 1, // positive past Unix time, not session sentinel -1
        },
      ]);
    } catch {
      writeError = true;
      break;
    }
  }
  const after = await context.cookies();
  return { before, selected, after, writeError };
}

// Diseño v2.5: GET with one exact temporary handler, including exceptional exits.
async function temporaryReload(page, url, timeout = 8000) {
  const handler = (route) => route.fallback();
  await page.route('**/*', handler);
  try {
    await page.goto(url, { waitUntil: 'load', timeout });
  } finally {
    await page.unroute('**/*', handler);
  }
}

const TARGET = 'target.parent.test';
const CHILD = 'child.target.parent.test';
const SIBLING = 'sibling.parent.test';
const PARENT = 'parent.test';
const OTHER = 'other.test';
const rows = [];
function result(id, ok, details = {}) {
  const status = typeof ok === 'string' ? ok : ok ? 'PASS' : 'FAIL';
  rows.push({ id, status, ...details });
  console.log(JSON.stringify(rows.at(-1)));
}
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function bounded(promise, ms = 10000) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('fixture_signal_timeout')), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}
const key = (cookie) => JSON.stringify([cookie.name, cookie.domain, cookie.path]);
const tokens = (header) => (header || '').split(/;\s*/).filter(Boolean).sort();
const names = (header) => tokens(header).map((token) => token.split('=')[0]);

function selectorChecks() {
  for (const [domain, host, expected] of [
    [TARGET, TARGET, true],
    [`.${TARGET}`, TARGET, true],
    [`.${PARENT}`, TARGET, true],
    [PARENT, TARGET, false],
    [SIBLING, TARGET, false],
    [`.${OTHER}`, TARGET, false],
    ['.notparent.test', TARGET, false],
    ['.parent.test', 'evilparent.test', false],
    ['.parent.test', PARENT, true],
    ['.xn--bcher-kva.test', 'a.xn--bcher-kva.test', true],
    ['127.0.0.1', '127.0.0.1', true],
    ['.127.0.0.1', '127.0.0.1', false],
    ['.0.0.1', '127.0.0.1', false],
    ['[::1]', '[::1]', true],
  ])
    assert.equal(domainMatches(domain, host), expected, `${domain} / ${host}`);
  result('selector_pure_contract', true);
}

async function origins() {
  const dir = mkdtempSync(join(tmpdir(), 'sg-g1-'));
  try {
    execFileSync(
      'openssl',
      [
        'req',
        '-x509',
        '-newkey',
        'rsa:2048',
        '-nodes',
        '-keyout',
        join(dir, 'key'),
        '-out',
        join(dir, 'cert'),
        '-days',
        '1',
        '-subj',
        '/CN=synthetic-g1.test',
        '-addext',
        `subjectAltName=DNS:${TARGET},DNS:${CHILD},DNS:${SIBLING},DNS:${PARENT},DNS:${OTHER},IP:127.0.0.1`,
      ],
      { stdio: 'ignore' },
    );
    const requests = [];
    const gates = new Map();
    const handle = async (req, res) => {
      const host = req.headers.host.split(':')[0];
      const url = new URL(req.url, `https://${host}`);
      const receipt = {
        host,
        path: url.pathname,
        cookie: req.headers.cookie || '',
        ifNoneMatch: req.headers['if-none-match'] || null,
      };
      requests.push(receipt);
      console.log(
        JSON.stringify({
          server: {
            host,
            path: receipt.path,
            cookieNames: names(receipt.cookie),
            ifNoneMatch: receipt.ifNoneMatch,
          },
        }),
      );
      const gate = gates.get(url.pathname);
      if (gate) {
        gate.entered.resolve();
        await gate.release.promise;
      }
      if (res.destroyed) return;
      res.setHeader('Cache-Control', 'no-store');
      res.setHeader('Content-Type', 'text/html');
      if (url.pathname.startsWith('/asset/')) {
        res.setHeader('Cache-Control', 'public, max-age=3600');
        res.setHeader('ETag', '"sg-g1-v1"');
        res.setHeader('Content-Type', 'image/svg+xml');
        if (req.headers['if-none-match'] === '"sg-g1-v1"') {
          res.writeHead(304);
          res.end();
        } else
          res.end(
            '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1"/></svg>',
          );
      } else if (url.pathname.startsWith('/cache/')) {
        res.end(`<!doctype html><img src="/asset/${url.pathname.split('/').at(-1)}.svg">`);
      } else if (url.pathname === '/chips/seed') {
        res.setHeader(
          'Set-Cookie',
          `sg_partition_twin=${url.searchParams.get('value')}; Path=/; Secure; HttpOnly; SameSite=None${url.searchParams.has('partitioned') ? '; Partitioned' : ''}`,
        );
        res.end(
          '<!doctype html><body><script>fetch("/chips/read").then(() => document.body.dataset.done="yes")</script>',
        );
      } else if (url.pathname === '/chips/top') {
        const path = url.searchParams.has('seed')
          ? `/chips/seed?value=${url.searchParams.get('seed')}&partitioned=1`
          : '/chips/read';
        const site = url.searchParams.get('site') === SIBLING ? SIBLING : TARGET;
        res.end(`<!doctype html><iframe id="partition" src="${secure(site, path)}"></iframe>`);
      } else res.end('<!doctype html><body data-done="yes">synthetic G1</body>');
    };
    const plain = httpServer((...args) => {
      void handle(...args);
    });
    const tls = httpsServer(
      { key: readFileSync(join(dir, 'key')), cert: readFileSync(join(dir, 'cert')) },
      (...args) => {
        void handle(...args);
      },
    );
    await new Promise((resolve) => plain.listen(0, '0.0.0.0', resolve));
    await new Promise((resolve) => tls.listen(0, '0.0.0.0', resolve));
    function secure(host, path) {
      return `https://${host}:${tls.address().port}${path}`;
    }
    return {
      requests,
      gates,
      secure,
      plain: (host, path) => `http://${host}:${plain.address().port}${path}`,
      count: (path) => requests.filter((row) => row.path === path).length,
      async close() {
        for (const gate of gates.values()) gate.release.resolve();
        plain.closeAllConnections();
        tls.closeAllConnections();
        await Promise.all([
          new Promise((resolve) => plain.close(resolve)),
          new Promise((resolve) => tls.close(resolve)),
        ]);
        rmSync(dir, { recursive: true, force: true });
      },
    };
  } catch (error) {
    rmSync(dir, { recursive: true, force: true });
    throw error;
  }
}

async function cookieCases(browser, origin) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  try {
    const page = await context.newPage();
    const allParent = [TARGET, CHILD, SIBLING, PARENT];
    // hosts/selected are independent expected behavior, not generated by selector.
    const specs = [
      { id: 'host_only', name: 'sg_host', domain: TARGET, hosts: [TARGET], selected: true, secure: false },
      {
        id: 'domain_target',
        name: 'sg_domain',
        domain: `.${TARGET}`,
        hosts: [TARGET, CHILD],
        selected: true,
      },
      { id: 'domain_parent', name: 'sg_parent', domain: `.${PARENT}`, hosts: allParent, selected: true },
      { id: 'sibling_host_only', name: 'sg_sibling', domain: SIBLING, hosts: [SIBLING], selected: false },
      { id: 'parent_host_only', name: 'sg_parent_host', domain: PARENT, hosts: [PARENT], selected: false },
      { id: 'path_root', name: 'sg_path', domain: TARGET, hosts: [TARGET], selected: true },
      { id: 'path_deep', name: 'sg_path', domain: TARGET, path: '/deep', hosts: [TARGET], selected: true },
      {
        id: 'httponly',
        name: 'sg_http_only',
        domain: TARGET,
        hosts: [TARGET],
        httpOnly: true,
        selected: true,
      },
      { id: 'secure', name: 'sg_secure', domain: TARGET, hosts: [TARGET], selected: true },
      ...['Strict', 'Lax', 'None'].map((sameSite) => ({
        id: `samesite_${sameSite}`,
        name: `sg_${sameSite}`,
        domain: TARGET,
        hosts: [TARGET],
        sameSite,
        selected: true,
      })),
      {
        id: 'prefix_host',
        name: '__Host-sg_host',
        domain: TARGET,
        hosts: [TARGET],
        httpOnly: true,
        selected: true,
      },
      {
        id: 'prefix_secure',
        name: '__Secure-sg_secure',
        domain: `.${TARGET}`,
        hosts: [TARGET, CHILD],
        selected: true,
      },
      { id: 'other_site', name: 'sg_other', domain: OTHER, hosts: [OTHER], selected: false },
      { id: 'collision_selected', name: 'sg_collision', domain: TARGET, hosts: [TARGET], selected: true },
      { id: 'collision_sibling', name: 'sg_collision', domain: SIBLING, hosts: [SIBLING], selected: false },
      { id: 'collision_parent_host', name: 'sg_collision', domain: PARENT, hosts: [PARENT], selected: false },
    ].map((spec) => ({
      path: '/',
      secure: true,
      httpOnly: false,
      sameSite: 'Lax',
      ...spec,
      value: randomBytes(12).toString('hex'),
      expires: Math.floor(Date.now() / 1000) + 3600,
    }));
    await context.addCookies(
      specs.map(({ name, value, domain, path, secure, httpOnly, sameSite, expires }) => ({
        name,
        value,
        domain,
        path,
        secure,
        httpOnly,
        sameSite,
        expires,
      })),
    );
    const probes = [TARGET, CHILD, SIBLING, PARENT, OTHER].flatMap((host) =>
      ['/cookie', '/deep/cookie'].map((path) => ({ host, path, secure: true })),
    );
    probes.push({ host: TARGET, path: '/deep/cookie', secure: false });
    async function observe() {
      const observations = [];
      for (const probe of probes) {
        const start = origin.requests.length;
        await page.goto((probe.secure ? origin.secure : origin.plain)(probe.host, probe.path), {
          timeout: 8000,
        });
        const received = origin.requests
          .slice(start)
          .find((row) => row.host === probe.host && row.path === probe.path);
        assert.ok(received, 'real server must receive cookie probe');
        observations.push({ ...probe, tokens: tokens(received.cookie) });
      }
      return observations;
    }
    const beforeHTTP = await observe();
    const deletion = await expireSelected(context, TARGET);
    const afterHTTP = await observe();
    const expectedTokens = (probe, after) =>
      specs
        .filter(
          (spec) =>
            (!after || !spec.selected) &&
            spec.hosts.includes(probe.host) &&
            (spec.path === '/' || probe.path.startsWith(`${spec.path}/`)) &&
            (!spec.secure || probe.secure),
        )
        .map((spec) => `${spec.name}=${spec.value}`)
        .sort();
    for (const [phase, observations] of [
      ['before', beforeHTTP],
      ['after', afterHTTP],
    ]) {
      result(
        `P1_headers_${phase}`,
        observations.every(
          (probe) =>
            JSON.stringify(probe.tokens) === JSON.stringify(expectedTokens(probe, phase === 'after')),
        ),
        { probes: observations.length },
      );
    }
    for (const spec of specs) {
      const before = deletion.before.find((cookie) => key(cookie) === key(spec));
      const after = deletion.after.find((cookie) => key(cookie) === key(spec));
      const selected = deletion.selected.some((cookie) => key(cookie) === key(spec));
      const baseline =
        before?.value === spec.value &&
        before.secure === spec.secure &&
        before.httpOnly === spec.httpOnly &&
        before.sameSite === spec.sameSite &&
        beforeHTTP.every(
          (probe) =>
            probe.tokens.includes(`${spec.name}=${spec.value}`) ===
            expectedTokens(probe, false).includes(`${spec.name}=${spec.value}`),
        );
      const browserOK = afterHTTP.every(
        (probe) =>
          probe.tokens.includes(`${spec.name}=${spec.value}`) ===
          expectedTokens(probe, true).includes(`${spec.name}=${spec.value}`),
      );
      result(
        `P1_${spec.id}`,
        baseline &&
          selected === spec.selected &&
          browserOK &&
          (spec.selected ? !after : JSON.stringify(before) === JSON.stringify(after)),
        { baseline, selected, expectedSelected: spec.selected, apiPresentAfter: Boolean(after), browserOK },
      );
    }
    result(
      'P1_expiry_writes_and_all_path_readback',
      !deletion.writeError && !deletion.after.some((cookie) => domainMatches(cookie.domain, TARGET)),
      {
        enumerated: deletion.before.length,
        selected: deletion.selected.length,
        remaining: deletion.after.length,
        writeError: deletion.writeError,
      },
    );
  } finally {
    await context.close();
  }
}

async function ipCase(browser, origin) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  try {
    const page = await context.newPage();
    const cookies = [
      { name: 'sg_ip', value: randomBytes(12).toString('hex'), domain: '127.0.0.1', path: '/' },
      { name: 'sg_ip_other', value: randomBytes(12).toString('hex'), domain: OTHER, path: '/' },
    ];
    await context.addCookies(cookies);
    async function read(host) {
      const start = origin.requests.length;
      await page.goto(origin.plain(host, '/ip/cookie'), { timeout: 8000 });
      return tokens(
        origin.requests.slice(start).find((row) => row.host === host && row.path === '/ip/cookie').cookie,
      );
    }
    const before = await read('127.0.0.1');
    const otherBefore = await read(OTHER);
    const deletion = await expireSelected(context, '127.0.0.1');
    const after = await read('127.0.0.1');
    const otherAfter = await read(OTHER);
    result(
      'P1_ip_exact_and_other_preserved',
      before.includes(`sg_ip=${cookies[0].value}`) &&
        after.length === 0 &&
        JSON.stringify(otherBefore) === JSON.stringify([`sg_ip_other=${cookies[1].value}`]) &&
        JSON.stringify(otherAfter) === JSON.stringify(otherBefore) &&
        !deletion.writeError &&
        deletion.selected.length === 1 &&
        deletion.after.length === 1 &&
        deletion.after[0].value === cookies[1].value,
    );
  } finally {
    await context.close();
  }
}

function partitionReceipt(origin, start) {
  const receipt = origin.requests
    .slice(start)
    .find((row) => row.host === TARGET && row.path === '/chips/read');
  assert.ok(receipt, 'partition probe must reach real origin');
  return tokens(receipt.cookie);
}
function twinPresence(received, unpartitionedToken, partitionedToken) {
  return {
    unpartitioned: received.includes(unpartitionedToken),
    partitioned: received.includes(partitionedToken),
  };
}

async function partitionCase(browser, origin) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  try {
    const page = await context.newPage();
    const unpartitioned = randomBytes(12).toString('hex');
    const partitioned = randomBytes(12).toString('hex');
    const unpartitionedToken = `sg_partition_twin=${unpartitioned}`;
    const partitionedToken = `sg_partition_twin=${partitioned}`;
    await page.goto(origin.secure(TARGET, `/chips/seed?value=${unpartitioned}`), { timeout: 8000 });
    await page.locator('body[data-done="yes"]').waitFor({ state: 'attached', timeout: 8000 });
    let start = origin.requests.length;
    await page.goto(origin.secure(OTHER, `/chips/top?seed=${partitioned}`), { timeout: 8000 });
    await page
      .frameLocator('#partition')
      .locator('body[data-done="yes"]')
      .waitFor({ state: 'attached', timeout: 8000 });
    const partitionBefore = partitionReceipt(origin, start);
    start = origin.requests.length;
    await page.goto(origin.secure(TARGET, '/chips/read'), { timeout: 8000 });
    const directBefore = partitionReceipt(origin, start);
    const established =
      partitionBefore.includes(partitionedToken) &&
      directBefore.includes(unpartitionedToken) &&
      !directBefore.includes(partitionedToken);
    if (!established) {
      result('P1_partition_twin', 'UNVERIFIED', {
        reason: 'Set-Cookie Partitioned in cross-site iframe did not independently establish distinct twins',
        iframeCookieNames: partitionBefore.map((token) => token.split('=')[0]),
        directCookieNames: directBefore.map((token) => token.split('=')[0]),
      });
      return;
    }
    const deletion = await expireSelected(context, TARGET);
    start = origin.requests.length;
    await page.goto(origin.secure(TARGET, '/chips/read'), { timeout: 8000 });
    const directAfter = partitionReceipt(origin, start);
    start = origin.requests.length;
    await page.goto(origin.secure(OTHER, '/chips/top'), { timeout: 8000 });
    await page
      .frameLocator('#partition')
      .locator('body[data-done="yes"]')
      .waitFor({ state: 'attached', timeout: 8000 });
    const partitionAfter = partitionReceipt(origin, start);
    result(
      'P1_partition_twin',
      !deletion.writeError &&
        directAfter.length === 0 &&
        partitionAfter.includes(partitionedToken) &&
        !partitionAfter.includes(unpartitionedToken),
      {
        independentlyEstablished: true,
        cookies: 'deleted_with_residual',
        beforeDirect: twinPresence(directBefore, unpartitionedToken, partitionedToken),
        beforeIframe: twinPresence(partitionBefore, unpartitionedToken, partitionedToken),
        afterIframe: twinPresence(partitionAfter, unpartitionedToken, partitionedToken),
        apiTwinsBefore: deletion.before.filter((c) => c.name === 'sg_partition_twin').length,
        apiPartitionIdentity: deletion.before.some((c) => 'partitionKey' in c),
        directPresentAfter: directAfter.length > 0,
        partitionPresentAfter: partitionAfter.length > 0,
        apiTwinsAfter: deletion.after.filter((c) => c.name === 'sg_partition_twin').length,
      },
    );
  } finally {
    await context.close();
  }
}

// Both arms use the production persistence primitive, storageState(), and a
// genuinely fresh context. HTTP receipts are independent of cookies() readback.
async function checkpointArm(browser, origin, reset) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  let restored;
  try {
    const page = await context.newPage();
    const unpartitioned = randomBytes(12).toString('hex');
    const partitioned = randomBytes(12).toString('hex');
    const unrelated = randomBytes(12).toString('hex');
    const seed = async (site, value, embedded) => {
      const url = embedded
        ? origin.secure(OTHER, `/chips/top?site=${site}&seed=${value}`)
        : origin.secure(site, `/chips/seed?value=${value}`);
      await page.goto(url, { timeout: 8000 });
      const locator = embedded
        ? page.frameLocator('#partition').locator('body[data-done="yes"]')
        : page.locator('body[data-done="yes"]');
      await locator.waitFor({ state: 'attached', timeout: 8000 });
    };
    await seed(TARGET, unpartitioned, false);
    await seed(TARGET, partitioned, true);
    await seed(SIBLING, unrelated, true);
    async function observe(probePage) {
      const seen = {};
      for (const [id, site, embedded] of [
        ['direct', TARGET, false],
        ['embedded', TARGET, true],
        ['unrelatedDirect', SIBLING, false],
        ['unrelatedEmbedded', SIBLING, true],
      ]) {
        const start = origin.requests.length;
        await probePage.goto(
          embedded ? origin.secure(OTHER, `/chips/top?site=${site}`) : origin.secure(site, '/chips/read'),
          { timeout: 8000 },
        );
        if (embedded)
          await probePage
            .frameLocator('#partition')
            .locator('body')
            .waitFor({ state: 'attached', timeout: 8000 });
        const receipt = origin.requests
          .slice(start)
          .find((row) => row.host === site && row.path === '/chips/read');
        assert.ok(receipt, 'checkpoint probe must reach real origin');
        const received = tokens(receipt.cookie);
        seen[id] = {
          unpartitioned: received.includes(`sg_partition_twin=${unpartitioned}`),
          partitioned: received.includes(`sg_partition_twin=${partitioned}`),
          unrelated: received.includes(`sg_partition_twin=${unrelated}`),
        };
      }
      return seen;
    }
    const before = await observe(page);
    assert.ok(before.direct.unpartitioned && !before.direct.partitioned && before.embedded.partitioned);
    assert.ok(!before.unrelatedDirect.unrelated && before.unrelatedEmbedded.unrelated);
    let deletion;
    if (reset) deletion = await expireSelected(context, TARGET);
    const live = await observe(page);
    const state = await context.storageState();
    restored = await browser.newContext({ ignoreHTTPSErrors: true, storageState: state });
    const after = await observe(await restored.newPage());
    const liveOK = reset
      ? [
          !deletion.writeError,
          !live.direct.unpartitioned,
          !live.direct.partitioned,
          live.embedded.partitioned,
          !live.embedded.unpartitioned,
        ].every(Boolean)
      : true;
    result(
      `G1b_${reset ? 'reset' : 'baseline'}_checkpoint_restore`,
      liveOK && live.unrelatedEmbedded.unrelated,
      {
        live,
        restored: after,
        residualFlattened: reset && after.direct.partitioned,
        baselineFlattensWithoutReset: !reset && after.direct.partitioned,
        unrelatedPartitionFlattened: after.unrelatedDirect.unrelated,
      },
    );
    return { live, after };
  } finally {
    if (restored) await restored.close();
    await context.close();
  }
}
async function checkpointCases(browser, origin) {
  const baseline = await checkpointArm(browser, origin, false);
  const reset = await checkpointArm(browser, origin, true);
  const residualFlattened = reset.after.direct.partitioned;
  const unrelatedPartitionFlattened = baseline.after.unrelatedDirect.unrelated;
  const receiptPatternEquivalent =
    reset.after.direct.partitioned === baseline.after.unrelatedDirect.unrelated &&
    reset.after.embedded.partitioned === baseline.after.unrelatedEmbedded.unrelated;
  result(
    'G1b_reset_vs_existing_baseline',
    residualFlattened === unrelatedPartitionFlattened &&
      receiptPatternEquivalent &&
      JSON.stringify(reset.after.unrelatedDirect) === JSON.stringify(baseline.after.unrelatedDirect) &&
      JSON.stringify(reset.after.unrelatedEmbedded) === JSON.stringify(baseline.after.unrelatedEmbedded) &&
      !reset.after.direct.unpartitioned &&
      !reset.after.embedded.unpartitioned,
    {
      residualFlattened,
      unrelatedPartitionFlattened,
      receiptPatternEquivalent,
      resetWorsensPartitionExposure: reset.after.direct.partitioned && !baseline.after.direct.partitioned,
    },
  );
}

async function cacheCases(browser, origin) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  try {
    const page = await context.newPage();
    const second = await context.newPage();
    const url = origin.secure(TARGET, '/cache/primary');
    const secondURL = origin.secure(TARGET, '/cache/second');
    const counts = () => ({
      primary: origin.count('/asset/primary.svg'),
      second: origin.count('/asset/second.svg'),
    });
    await page.goto(url, { timeout: 8000 });
    await second.goto(secondURL, { timeout: 8000 });
    const warm = counts();
    await page.goto(url, { timeout: 8000 });
    await second.goto(secondURL, { timeout: 8000 });
    const ordinary = counts();
    result(
      'P2_negative_control_ordinary_goto_cache',
      warm.primary === 1 &&
        ordinary.primary === warm.primary &&
        warm.second === 1 &&
        ordinary.second === warm.second,
      { warm, ordinary },
    );
    const gate = { entered: deferred(), release: deferred() };
    origin.gates.set('/cache/primary', gate);
    const operation = temporaryReload(page, url).then(
      () => ({ ok: true }),
      () => ({ ok: false, code: 'reload_failed' }),
    );
    let during;
    try {
      await bounded(gate.entered.promise);
      await second.goto(secondURL, { timeout: 8000 });
      during = counts();
    } finally {
      gate.release.resolve();
      origin.gates.delete('/cache/primary');
    }
    const outcome = await operation;
    const reset = counts();
    result('P2_temporary_route_refetch', outcome.ok && reset.primary === ordinary.primary + 1, {
      outcome,
      ordinary,
      reset,
    });
    await page.goto(url, { timeout: 8000 });
    await second.goto(secondURL, { timeout: 8000 });
    const later = counts();
    result('P2_cache_reenabled_after_unroute', later.primary === reset.primary, { reset, later });
    result(
      'P2_second_tab_during_and_after',
      during.second === warm.second && reset.second === warm.second && later.second === warm.second,
      { warm, during, reset, later },
    );

    // Existing handlers inherently disable this page's cache. Test preservation
    // separately so they cannot invalidate the negative control above.
    let previousHits = 0;
    const previous = (route) => {
      previousHits++;
      return route.fallback();
    };
    await page.route('**/*', previous); // same pattern: exact-handler removal matters
    let hits = previousHits;
    await temporaryReload(page, url);
    const hitsDuring = previousHits - hits;
    hits = previousHits;
    await page.goto(url, { timeout: 8000 });
    result('P2_previous_handler_success', hitsDuring >= 2 && previousHits - hits >= 2, {
      hitsDuring,
      hitsAfter: previousHits - hits,
    });

    const stalled = { entered: deferred(), release: deferred() };
    origin.gates.set('/cache/timeout', stalled);
    const timeoutResult = temporaryReload(page, origin.secure(TARGET, '/cache/timeout'), 700).then(
      () => ({ unexpectedSuccess: true }),
      () => ({ code: 'reload_timeout' }),
    );
    try {
      await bounded(stalled.entered.promise);
      await timeoutResult;
    } finally {
      stalled.release.resolve();
      origin.gates.delete('/cache/timeout');
    }
    const timeout = await timeoutResult;
    hits = previousHits;
    await page.goto(url, { timeout: 8000 });
    result(
      'P2_previous_handler_after_timeout',
      timeout.code === 'reload_timeout' && previousHits - hits >= 2,
      { timeout, hitsAfter: previousHits - hits },
    );
    await page.unroute('**/*', previous);
    await page.goto(url, { timeout: 8000 });
    const afterTimeoutWarm = counts();
    await page.goto(url, { timeout: 8000 });
    result('P2_no_temporary_route_leak_after_timeout', counts().primary === afterTimeoutWarm.primary, {
      afterTimeoutWarm,
      later: counts(),
    });

    // Also test finally when the actual target page closes during server I/O.
    const closing = await context.newPage();
    let closingHits = 0;
    const closingPrevious = (route) => {
      closingHits++;
      return route.fallback();
    };
    await closing.route('**/*', closingPrevious);
    const closeGate = { entered: deferred(), release: deferred() };
    origin.gates.set('/cache/closing', closeGate);
    const closeResult = temporaryReload(closing, origin.secure(TARGET, '/cache/closing')).then(
      () => ({ unexpectedSuccess: true }),
      () => ({ code: 'page_closed' }),
    );
    try {
      await bounded(closeGate.entered.promise);
      await closing.close();
    } finally {
      closeGate.release.resolve();
      origin.gates.delete('/cache/closing');
    }
    const closed = await closeResult;
    result(
      'P2_close_mid_goto_cleanup',
      closing.isClosed() && closed.code === 'page_closed' && closingHits > 0,
      { closed, previousHitsBeforeClose: closingHits },
    );
    await second.goto(secondURL, { timeout: 8000 });
    result('P2_second_tab_after_close', counts().second === warm.second, { counts: counts() });
    result('P2_closed_page_previous_handler_invocation', 'UNVERIFIED', {
      reason:
        'A closed page cannot issue later requests. Exact-handler unroute completed, but post-close invocation on that page is impossible.',
    });
  } finally {
    await context.close();
  }
}

async function main() {
  selectorChecks();
  console.log(
    JSON.stringify({
      fixture: 'site-reset-G1',
      node: process.version,
      sha256: createHash('sha256')
        .update(readFileSync(fileURLToPath(import.meta.url)))
        .digest('hex'),
      network: 'none; real loopback HTTP/HTTPS; synthetic only',
      boundary:
        'No service workers, route implementation, live stack or Hermes tested; G1b tests storageState restore',
    }),
  );
  const require = createRequire('/app/package.json');
  const { Camoufox } = await import(pathToFileURL(require.resolve('camoufox-js')));
  console.log(
    JSON.stringify({
      camoufox: require('camoufox-js/package.json').version,
      playwright: require('playwright-core/package.json').version,
    }),
  );
  const deadline = setTimeout(() => {
    console.error('fixture_deadline');
    process.exit(2);
  }, 180000);
  deadline.unref();
  let origin, browser;
  try {
    origin = await origins();
    console.log('fixture_launch_real_Camoufox');
    browser = await Camoufox({ headless: 'virtual', geoip: false });
    console.log(JSON.stringify({ browserVersion: browser.version() }));
    for (const [id, test] of [
      ['P1_cookie_cases', cookieCases],
      ['P1_ip_case', ipCase],
      ['P1_partition_case', partitionCase],
      ['G1b_checkpoint_restore', checkpointCases],
      ['P2_cache_cases', cacheCases],
    ]) {
      try {
        await test(browser, origin);
      } catch {
        result(id, false, { code: 'fixture_case_failed' });
      }
    }
  } finally {
    if (browser) await browser.close();
    if (origin) await origin.close();
    clearTimeout(deadline);
  }
  for (const name of ['memory.peak', 'memory.events', 'pids.peak']) {
    try {
      console.log(
        JSON.stringify({ resource: name, value: readFileSync(`/sys/fs/cgroup/${name}`, 'utf8').trim() }),
      );
    } catch {
      /* Resource telemetry is informative, not the primitive verdict. */
    }
  }
  const failed = rows.filter((row) => row.status === 'FAIL').map((row) => row.id);
  const unverified = rows.filter((row) => row.status === 'UNVERIFIED').map((row) => row.id);
  console.log(
    JSON.stringify({
      summary: failed.length ? 'FAIL' : unverified.length ? 'PASS_WITH_GAPS' : 'PASS',
      failed,
      unverified,
      safeToBuildRoute: failed.length === 0 && unverified.length === 0,
    }),
  );
  if (failed.length) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => {
    console.error(JSON.stringify({ code: 'fixture_fatal' }));
    process.exitCode = 2;
  });
}
