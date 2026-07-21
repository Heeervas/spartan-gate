const assert = require('assert');
const test = require('node:test');
const vm = require('vm');

const { patchOperatorLeaseApi } = require('./patch-operator-lease-api.js');

function fixture() {
  return `// SPARTAN_OPERATOR_LEASE_V1
const SPARTAN_OPERATOR_GROUP = 'spartan-operator-login';
const SPARTAN_OPERATOR_LEASE_MS = 30 * 60 * 1000;
const capabilities = {
    operatorTabIsolationVersion: 1,
    operatorLeaseMs: SPARTAN_OPERATOR_LEASE_MS,
    nonDestructiveTabCreate: true,
};
app.post('/tabs', async (req, res) => {
});
`;
}

test('lease renewal API is idempotent, scoped to operator tabs, and versioned', async () => {
  const patched = patchOperatorLeaseApi(fixture());
  assert.strictEqual(patchOperatorLeaseApi(patched), patched);
  assert.match(patched, /SPARTAN_OPERATOR_LEASE_API_V1/);
  assert.match(patched, /operatorLeaseRenewal: true/);

  const routes = new Map();
  const operator = { _spartanOperatorLeaseUntil: 0 };
  let selected = { listItemId: 'spartan-operator-login', tabState: operator };
  const response = {
    statusCode: 200,
    body: null,
    status(code) { this.statusCode = code; return this; },
    json(body) { this.body = body; return this; },
  };
  const context = {
    app: { post(path, ...handlers) { routes.set(path, handlers.at(-1)); } },
    authMiddleware: () => () => {},
    sessions: new Map([['profile', {}]]),
    normalizeUserId: value => String(value),
    findTab: () => selected,
  };
  vm.runInNewContext(`${patched}\nglobalThis.capabilities = capabilities;`, context);
  assert.strictEqual(context.capabilities.operatorLeaseRenewal, true);
  const handler = routes.get('/tabs/:tabId/spartan/operator-lease');
  assert.ok(handler);

  await handler({ body: { userId: 'profile' }, params: { tabId: 'tab' } }, response);
  assert.strictEqual(response.statusCode, 200);
  assert.strictEqual(response.body.leaseMs, 30 * 60 * 1000);
  assert.ok(operator._spartanOperatorLeaseUntil > Date.now());

  selected = { listItemId: 'hermes-session', tabState: {} };
  response.statusCode = 200;
  await handler({ body: { userId: 'profile' }, params: { tabId: 'tab' } }, response);
  assert.strictEqual(response.statusCode, 409);
});
