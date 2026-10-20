const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/web/assets/dashboard.js`, 'utf8');
const context = {};
vm.runInNewContext(html.slice(html.indexOf('    function cacheUsagePercent('), html.indexOf('    function cacheLowSummary(')), context);
const details = fs.readFileSync(`${__dirname}/web/assets/dashboard-details.js`, 'utf8');
vm.runInNewContext(details.slice(details.indexOf('    function nativeCacheDetail('), details.indexOf('// Loaded on the first turn expansion')), context);

test('native checks report missing observations without inventing zero cache writes', () => {
  assert.equal(context.nativeCacheSummary(), 'no native comparisons yet');
  assert.equal(context.cacheUsagePercent(200, 50, {inputTokens: 200, cacheReadInputTokens: 100, cacheReadMissingRequests: 1}), '50.0% · partial');
  assert.equal(context.cacheUsagePercent(100, 0, {inputTokens: 100, cacheReadInputTokens: 0, cacheReadMissingRequests: 1}), 'unknown');
  assert.equal(context.cacheUsagePercent(60095, 0, {inputTokens: 60095, cacheReadInputTokens: 60095, cacheReadMissingRequests: 0}), '0.0%');
  assert.match(context.nativeCacheSummary({requests: 4, comparisons: {replay: 1, append: 1, changed: 1, unknown: 1}}), /2 compatible · 1 changed · 1 unknown/);
  assert.match(context.nativeCacheDetail(null), /Legacy/);
  assert.match(context.nativeCacheDetail({version: 2, comparison: 'unknown'}), /cache writes unknown/);
  assert.match(context.nativeCacheDetail({version: 2, comparison: 'replay', cache_write_tokens: 0}), /0 cache-write tokens/);
  assert.match(context.nativeCacheDetail({version: 2, comparison: 'changed', changed_components: ['instructions'], overlap: true}), /instructions · overlapping requests/);
});

test('conversation continuity is distinct from cache-key compatibility', () => {
  const detail = context.nativeCacheDetail({version: 2, comparison: 'no_predecessor', conversation_trace: {
    comparison: 'continued', cache_key_changed: true, changed_components: ['instructions', 'input'],
  }});
  assert.match(detail, /no completed predecessor for this cache key/);
  assert.match(detail, /conversation continued · cache key changed · instructions changed, input changed/);
  const ambiguous = context.nativeCacheDetail({version: 2, comparison: 'replay', conversation_trace: {
    comparison: 'unknown', cache_key_changed: true, changed_components: ['instructions'], overlap: true,
  }});
  assert.match(ambiguous, /conversation comparison unavailable · overlapping conversation requests/);
  assert.doesNotMatch(ambiguous, /conversation continued|cache key changed|instructions changed/);
});
