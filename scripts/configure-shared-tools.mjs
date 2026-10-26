#!/usr/bin/env node
// Opt-in only. Never selects a home directory, installs packages, or changes process env.
import fs from 'node:fs';
import { dirname, join, resolve, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const FAIL = 'Shared tools configuration rejected; check explicit paths, ownership, permissions and inputs.';
const reject = () => { throw new Error(FAIL); };
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function absolute(value) {
  if (typeof value !== 'string' || !isAbsolute(value) || /[\x00-\x1f\x7f]/.test(value)) reject();
  return resolve(value);
}

function inspect(path) {
  const parent = dirname(path);
  if (parent !== path) inspectDirectory(parent);
  try {
    const stat = fs.lstatSync(path);
    if (stat.isSymbolicLink()) reject();
    return stat;
  } catch (error) {
    if (error.code === 'ENOENT') return undefined;
    throw error;
  }
}

function ownerOnlyAncestor(path) {
  // inspect() already verified this same ancestor chain; never infer trust from
  // a sibling, a sticky directory, a symlink, or an unreadable/unknown stat.
  let current = dirname(path);
  for (;;) {
    const stat = fs.lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) reject();
    if (stat.uid === process.getuid() && (stat.mode & 0o7777) === 0o700) return true;
    const parent = dirname(current);
    if (parent === current) return false;
    current = parent;
  }
}

function inspectDirectory(path) {
  const stat = inspect(path);
  if (stat && (!stat.isDirectory() || (stat.mode & 0o022))) {
    // Ordinary root-owned system ancestors remain subject to the old checks.
    // Only a verified owner-only enclosure blocks untrusted group traversal.
    const enclosed = stat.isDirectory() && stat.uid === process.getuid()
      && !(stat.mode & 0o002) && ownerOnlyAncestor(path);
    // Preserve exactly the existing sticky-temporary-directory exception.
    if (!(stat.isDirectory() && (stat.mode & 0o1000)) && !enclosed) reject();
  }
  return stat;
}

function ownedDirectory(path, mode) {
  const stat = inspectDirectory(path);
  if (stat && (stat.uid !== process.getuid() || (mode && (stat.mode & 0o777) !== mode))) reject();
  let writable = path;
  while (!fs.existsSync(writable)) writable = dirname(writable);
  fs.accessSync(writable, fs.constants.W_OK | fs.constants.X_OK);
  return stat;
}

function readRegular(path, privateMode = false) {
  const stat = inspect(path);
  if (!stat) return undefined;
  if (!stat.isFile() || stat.uid !== process.getuid() || stat.nlink !== 1 || stat.size > 1024 * 1024) reject();
  if (privateMode ? (stat.mode & 0o777) !== 0o600 : Boolean(stat.mode & 0o022)) reject();
  const fd = fs.openSync(path, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const actual = fs.fstatSync(fd);
    if (actual.ino !== stat.ino || actual.dev !== stat.dev) reject();
    return { bytes: fs.readFileSync(fd), mode: stat.mode & 0o777 };
  } finally { fs.closeSync(fd); }
}

function jsonInput(path) {
  const input = readRegular(path, true);
  if (!input) return {};
  const value = JSON.parse(input.bytes.toString('utf8'));
  if (!object(value)) reject();
  return value;
}

function endpoint(value) {
  // Literal loopback only; URL normalization must not admit decimal/short IP aliases.
  if (typeof value !== 'string' || !/^https?:\/\/(127\.0\.0\.1|\[::1\])(?::[0-9]+)?\/?$/.test(value)) reject();
  const url = new URL(value);
  return url.origin;
}

function argumentsFor(argv) {
  const args = {};
  const values = ['agent-dir', 'base-url', 'client-id', 'credential-file', 'state-dir', 'backup-dir'];
  for (let index = 0; index < argv.length; index++) {
    const key = argv[index].replace(/^--/, '');
    if (argv[index] !== `--${key}` || Object.hasOwn(args, key)) reject();
    if (key === 'apply') args[key] = true;
    else if (values.includes(key) && argv[index + 1] && !argv[index + 1].startsWith('--')) args[key] = argv[++index];
    else reject();
  }
  for (const key of values.filter(key => key !== 'backup-dir')) if (!args[key]) reject();
  return args;
}

function strictSearch(web, baseUrl, token) {
  for (const key of ['webSearch', 'tools', 'commands', 'ssrf', 'toolNames']) {
    if (web[key] !== undefined && !object(web[key])) reject();
  }
  for (const [parent, keys] of [['tools', ['sourceCheck', 'fetchContent']], ['commands', ['websearch', 'curator', 'search', 'google-account']]]) {
    for (const key of keys) if (web[parent]?.[key] !== undefined && !object(web[parent][key])) reject();
  }
  if (web.toolNames?.webSearch && web.toolNames.webSearch !== 'web_search') reject();
  const merged = { ...web, provider: 'searxng', searchProvider: 'searxng', workflow: 'none', searxngBaseUrl: baseUrl,
    // Prior routing is owned search state: disabled providers break native load
    // even with an explicit SearXNG default. Remove it, not the provider allowlist.
    webSearch: { ...web.webSearch, allowedProviders: ['searxng'] }, searchRouting: undefined, proxy: undefined,
    ssrf: { ...web.ssrf, trustEnvProxy: false, allowRanges: [] } };
  for (const [parent, keys] of [['tools', ['sourceCheck', 'fetchContent']], ['commands', ['websearch', 'curator', 'search', 'google-account']]]) {
    merged[parent] = { ...web[parent] };
    for (const key of keys) merged[parent][key] = { ...web[parent]?.[key], enabled: false };
  }
  // pi-web-access 0.44 only supports static searxngHeaders, not a credential-file
  // hook. Duplicate ONLY the scoped client bearer here; protect this file/backups
  // like the credential file. Rotate both together using this configuration owner.
  merged.searxngHeaders = { Authorization: `Bearer ${token}` };
  return merged;
}

// Select and validate the client's explicit authority inputs, independent of
// source inventory and destination filesystem planning.
function clientInputs(args, env) {
  const agentDir = absolute(args['agent-dir']);
  if (!env.PI_CODING_AGENT_DIR || absolute(env.PI_CODING_AGENT_DIR) !== agentDir) {
    throw new Error('Set PI_CODING_AGENT_DIR to the selected --agent-dir for Pi runtime web-search.json discovery; no environment was changed.');
  }
  const baseUrl = endpoint(args['base-url']);
  if (env.SEARXNG_BASE_URL !== undefined && endpoint(env.SEARXNG_BASE_URL) !== baseUrl) {
    throw new Error('SEARXNG_BASE_URL conflicts with the selected ingress (values redacted); no files changed.');
  }
  const clientId = args['client-id'];
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(clientId)) reject();
  const credentialFile = absolute(args['credential-file']);
  const credential = readRegular(credentialFile, true);
  if (!credential || !/^[0-9a-f]{64}\n?$/.test(credential.bytes.toString('utf8'))) reject();
  const stateDir = absolute(args['state-dir']);
  return { agentDir, token: credential.bytes.toString('utf8').trim(), config: { version: 1, baseUrl, credentialFile, stateDir, clientId } };
}

function configurationFiles({ agentDir, token, config }) {
  const configPath = join(agentDir, 'spartan-tools.json');
  jsonInput(configPath);
  const webPath = join(agentDir, 'web-search.json');
  const web = strictSearch(jsonInput(webPath), config.baseUrl, token);
  const json = value => Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
  return [{ path: configPath, bytes: json(config), mode: 0o600 }, { path: webPath, bytes: json(web), mode: 0o600 }];
}

// Source inventory owns the installed extension/import layout and captures all
// program bytes before any destination can be written.
function extensionFiles(extensionDir) {
  const sourceDir = join(ROOT, 'infra/pi/extensions');
  const files = [];
  const helpers = fs.readdirSync(join(sourceDir, 'spartan-tools')).filter(name => name.endsWith('.mjs')).sort();
  if (!helpers.length) reject();
  for (const relative of ['spartan-tools.ts', ...helpers.map(name => `spartan-tools/${name}`)]) {
    const source = readRegular(join(sourceDir, relative));
    if (!source?.bytes.length) reject();
    files.push({ path: join(extensionDir, relative), bytes: source.bytes, mode: 0o644 });
  }
  return files;
}

function inspectTargets(files, { credentialFile, stateDir }) {
  for (const file of files) {
    if (file.path === credentialFile || file.path === stateDir || stateDir.startsWith(`${file.path}/`)) reject();
    file.before = readRegular(file.path, file.mode === 0o600);
    file.changed = !file.before || !file.bytes.equals(file.before.bytes) || file.mode !== file.before.mode;
  }
}

function backupLocation(args, files) {
  const backupDir = args['backup-dir'] ? absolute(args['backup-dir']) : undefined;
  if (backupDir) ownedDirectory(backupDir, 0o700);
  if (args.apply && files.some(file => file.changed && file.before) && !backupDir) reject();
  if (backupDir && files.some(file => backupDir === file.path || backupDir.startsWith(`${file.path}/`))) reject();
  return backupDir;
}

function plan(args, env) {
  const inputs = clientInputs(args, env);
  const { agentDir, config: { stateDir } } = inputs;
  const extensionDir = join(agentDir, 'extensions');
  const helperDir = join(extensionDir, 'spartan-tools');
  ownedDirectory(stateDir, 0o700);
  for (const directory of [agentDir, extensionDir, helperDir]) ownedDirectory(directory);
  const files = [...configurationFiles(inputs), ...extensionFiles(extensionDir)];
  inspectTargets(files, inputs.config);
  const backupDir = backupLocation(args, files);
  return { files, directories: [agentDir, extensionDir, helperDir, stateDir], backupDir, stateDir, configPath: join(agentDir, 'spartan-tools.json') };
}

function atomic(path, bytes, mode) {
  const temp = join(dirname(path), `.spartan-tools-${randomUUID()}.tmp`);
  try {
    fs.writeFileSync(temp, bytes, { flag: 'wx', mode });
    fs.chmodSync(temp, mode);
    fs.renameSync(temp, path);
  } finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
}

// Optimistic concurrency guard for the complete destination snapshot, before
// directory creation, backups, or atomic replacements. This is not a same-user
// hostile-process lock.
function recheckTargets(files) {
  for (const file of files) {
    const now = readRegular(file.path, file.mode === 0o600);
    if (Boolean(now) !== Boolean(file.before) || (now && (!now.bytes.equals(file.before.bytes) || now.mode !== file.before.mode))) reject();
  }
}

function writeBackups(files, backupDir, mkdir) {
  const overwritten = files.filter(file => file.before);
  if (!overwritten.length) return;
  mkdir(backupDir);
  for (const [index, file] of overwritten.entries()) {
    fs.writeFileSync(join(backupDir, `${randomUUID()}-${index}.bak`), file.before.bytes, { mode: 0o600, flag: 'wx' });
  }
}

// Restore only bytes still matching this invocation's replacement; never undo
// another writer's changes. Nonempty backup/foreign directories are retained.
function rollback(installed, directories) {
  for (const file of installed.reverse()) {
    const now = readRegular(file.path, file.mode === 0o600);
    if (!now || !now.bytes.equals(file.bytes) || now.mode !== file.mode) continue;
    if (file.before) atomic(file.path, file.before.bytes, file.before.mode);
    else fs.unlinkSync(file.path);
  }
  for (const path of directories.reverse()) {
    try { fs.rmdirSync(path); } catch { /* Retain nonempty backups/foreign changes. */ }
  }
}

function apply(plan) {
  const directories = [];
  const installed = [];
  function mkdir(path) {
    if (fs.existsSync(path)) return;
    mkdir(dirname(path));
    fs.mkdirSync(path, { mode: 0o700 });
    directories.push(path);
  }
  try {
    recheckTargets(plan.files);
    for (const path of plan.directories) mkdir(path);
    const changed = plan.files.filter(file => file.changed);
    writeBackups(changed, plan.backupDir, mkdir);
    for (const file of changed) {
      atomic(file.path, file.bytes, file.mode);
      installed.push(file);
    }
  } catch (error) {
    rollback(installed, directories);
    throw error;
  }
}

export function configure(argv, env = process.env, output = console.log) {
  try {
    const args = argumentsFor(argv);
    const prepared = plan(args, env);
    if (args.apply) apply(prepared);
    output(args.apply ? 'APPLY' : 'DRYRUN (no files changed)');
    for (const file of prepared.files) output(`${file.changed ? 'change' : 'nochange'} ${file.path}`);
    output(`state ${prepared.stateDir}`);
    output(`Pi runtime: set PI_CODING_AGENT_DIR to selected --agent-dir and PI_SPARTAN_TOOLS_CONFIG to ${prepared.configPath}; restart only the selected client after approval.`);
    return 0;
  } catch (error) {
    // Never echo raw parser/fs errors, argv, URLs, JSON, tokens, or secret hashes.
    output(error.message.startsWith('Set PI_CODING_AGENT_DIR') || error.message.startsWith('SEARXNG_BASE_URL conflicts') ? error.message : FAIL);
    return 1;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exitCode = configure(process.argv.slice(2));
