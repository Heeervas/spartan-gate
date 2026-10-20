const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/web/assets/dashboard.js`, 'utf8');
const start = html.indexOf('    function toolSummary(');
const end = html.indexOf('    function usageUnitSummary(', start);
const context = {};
vm.runInNewContext(html.slice(start, end), context);

test('tool counts distinguish complete, partial, and unknown sessions', () => {
  assert.equal(context.toolSummary({}), 'unknown');
  assert.equal(context.toolSummary({toolTelemetryKnownRequests: 1}), '0 calls');
  assert.equal(context.toolSummary({toolTelemetryKnownRequests: 1, toolTelemetryUnknownRequests: 1}), 'unknown');
  assert.equal(context.toolSummary({toolTelemetryPartialRequests: 1, actualToolCalls: 2}), '2+ calls · partial');
  assert.equal(context.toolSummary({toolTelemetryKnownRequests: 2, actualToolCalls: 3}), '3 calls');
  assert.equal(context.toolSummary({toolTelemetryKnownRequests: 1, toolTelemetryUnknownRequests: 1, actualToolCalls: 3}), '3+ calls · partial');
});
