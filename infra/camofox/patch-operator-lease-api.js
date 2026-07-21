const fs = require('fs');
const { spawnSync } = require('child_process');

const serverPath = process.argv[2] || 'server.js';
const MARKER = '// SPARTAN_OPERATOR_LEASE_API_V1';

function replaceOnce(source, from, to, label) {
  const count = source.split(from).length - 1;
  if (count !== 1) throw new Error(`${label}: expected one anchor, found ${count}`);
  return source.replace(from, to);
}

function patchOperatorLeaseApi(source) {
  if (source.includes(MARKER)) return source;
  if (!source.includes('// SPARTAN_OPERATOR_LEASE_V1')) {
    throw new Error('operator lease core must be patched before its API');
  }
  source = replaceOnce(
    source,
    `    operatorLeaseMs: SPARTAN_OPERATOR_LEASE_MS,
    nonDestructiveTabCreate: true,
`,
    `    operatorLeaseMs: SPARTAN_OPERATOR_LEASE_MS,
    operatorLeaseRenewal: true,
    nonDestructiveTabCreate: true,
`,
    'operator lease capability',
  );
  return replaceOnce(
    source,
    "app.post('/tabs', async (req, res) => {\n",
    `${MARKER}
app.post('/tabs/:tabId/spartan/operator-lease', authMiddleware(), async (req, res) => {
  const userId = req.body?.userId;
  if (!userId) return res.status(400).json({ error: 'userId required' });
  const session = sessions.get(normalizeUserId(userId));
  const found = session && findTab(session, req.params.tabId);
  if (!found) return res.status(404).json({ error: 'operator tab not found' });
  if (found.listItemId !== SPARTAN_OPERATOR_GROUP) {
    return res.status(409).json({ error: 'tab is not an operator login tab' });
  }
  found.tabState._spartanOperatorLeaseUntil = Date.now() + SPARTAN_OPERATOR_LEASE_MS;
  return res.json({ ok: true, leaseMs: SPARTAN_OPERATOR_LEASE_MS });
});

app.post('/tabs', async (req, res) => {
`,
    'operator lease renewal route',
  );
}

if (require.main === module) {
  const original = fs.readFileSync(serverPath, 'utf8');
  const patched = patchOperatorLeaseApi(original);
  const tmpPath = `${serverPath}.spartan-operator-lease-api.js`;
  try {
    fs.writeFileSync(tmpPath, patched);
    const checked = spawnSync(process.execPath, ['--check', tmpPath], { encoding: 'utf8' });
    if (checked.status !== 0) {
      throw new Error(`patched server syntax check failed: ${checked.stderr || checked.stdout}`);
    }
    fs.renameSync(tmpPath, serverPath);
  } finally {
    fs.rmSync(tmpPath, { force: true });
  }
}

module.exports = { patchOperatorLeaseApi };
