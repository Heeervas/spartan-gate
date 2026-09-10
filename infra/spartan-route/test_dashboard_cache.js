const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/web/dashboard.html`, 'utf8');
const context = {};
vm.runInNewContext(html.slice(html.indexOf('    function cacheUsagePercent('), html.indexOf('    function cacheLowSummary(')), context);

test('native checks report missing observations without inventing zero cache writes', () => {
  assert.equal(context.nativeCacheSummary(), 'no native comparisons yet');
  assert.equal(context.cacheUsagePercent(200, 50, {inputTokens: 200, cacheReadInputTokens: 100, cacheReadMissingRequests: 1}), '50.0% · partial');
  assert.equal(context.cacheUsagePercent(100, 0, {inputTokens: 100, cacheReadInputTokens: 0, cacheReadMissingRequests: 1}), 'unknown');
  assert.match(context.nativeCacheSummary({requests: 4, comparisons: {replay: 1, append: 1, changed: 1, unknown: 1}}), /2 compatible · 1 changed · 1 unknown/);
  assert.match(context.nativeCacheDetail(null), /Legacy/);
  assert.match(context.nativeCacheDetail({version: 2, comparison: 'unknown'}), /cache writes unknown/);
  assert.match(context.nativeCacheDetail({version: 2, comparison: 'replay', cache_write_tokens: 0}), /0 cache-write tokens/);
  assert.match(context.nativeCacheDetail({version: 2, comparison: 'changed', changed_components: ['instructions'], overlap: true}), /instructions · overlapping requests/);
});
