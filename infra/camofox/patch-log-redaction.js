const fs = require('fs');
const { spawnSync } = require('child_process');

const serverPath = process.argv[2] || 'server.js';

function replaceOnce(source, from, to, label) {
  const count = source.split(from).length - 1;
  if (count !== 1) throw new Error(`${label}: expected one anchor, found ${count}`);
  return source.replace(from, to);
}

function patchLogRedaction(source) {
  if (source.includes('SPARTAN_LOG_URL_REDACTION_V1')) return source;

  source = replaceOnce(
    source,
    `// --- Structured logging ---
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
`,
    `// --- Structured logging ---
// SPARTAN_LOG_URL_REDACTION_V1: OAuth callbacks and arbitrary page URLs can
// contain credentials in their query or fragment. Logs retain only origin/path.
function spartanRedactLogUrl(value) {
  if (typeof value !== 'string') return value;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return value;
    return \`\${parsed.origin}\${parsed.pathname}\`;
  } catch {
    return value.replace(/[?#].*$/, '');
  }
}

function spartanRedactLogValue(key, value) {
  if (typeof value !== 'string') return value;
  if (key === 'url' || key === 'path') return spartanRedactLogUrl(value);
  if (key === 'error' || key === 'stack') {
    return value.replace(/https?:\\/\\/[^\\s"'<>]+/gi, match => spartanRedactLogUrl(match));
  }
  return value;
}

function log(level, msg, fields = {}) {
  const safeFields = Object.fromEntries(
    Object.entries(fields).map(([key, value]) => [key, spartanRedactLogValue(key, value)]),
  );
  const entry = {
    ts: new Date().toISOString(),
    level,
    msg,
    ...safeFields,
  };
  const line = JSON.stringify(entry);
  if (level === 'error') {
    process.stderr.write(line + '\\n');
  } else {
    process.stdout.write(line + '\\n');
  }
}
`,
    'structured logger',
  );

  if (source.includes('      path: res.req?.originalUrl,')) {
    source = replaceOnce(source, '      path: res.req?.originalUrl,', '      path: res.req?.path,', 'legacy Sentry request path');
  } else {
    source = replaceOnce(source, '      path: req?.originalUrl,', '      path: req?.path,', 'Sentry response request path');
    source = replaceOnce(source, '    path: req.originalUrl,', '    path: req.path,', 'Sentry route request path');
  }
  return source;
}

if (require.main === module) {
  const original = fs.readFileSync(serverPath, 'utf8');
  const patched = patchLogRedaction(original);
  const tmpPath = `${serverPath}.spartan-log-redaction-tmp.js`;
  try {
    fs.writeFileSync(tmpPath, patched);
    const checked = spawnSync(process.execPath, ['--check', tmpPath], { encoding: 'utf8' });
    if (checked.status !== 0) {
      throw new Error(`patched syntax check failed: ${checked.stderr || checked.stdout}`);
    }
    fs.renameSync(tmpPath, serverPath);
  } finally {
    fs.rmSync(tmpPath, { force: true });
  }
}

module.exports = { patchLogRedaction };
