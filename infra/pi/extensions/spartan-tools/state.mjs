import { createHash, randomBytes } from 'node:crypto';
import { mkdir, lstat, realpath, open, rename, unlink } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { privateFile, TOKEN, FAILURE } from './config.mjs';

async function checkedDirectory(directory) {
  try { await mkdir(directory, { mode: 0o700 }); } catch (error) { if (error.code !== 'EEXIST') throw error; }
  const stat = await lstat(directory);
  if (!stat.isDirectory() || stat.uid !== process.getuid() || (stat.mode & 0o7777) !== 0o700 || await realpath(directory) !== resolve(directory)) throw new Error(FAILURE);
}

export function stateKey(config, sessionId) {
  if (typeof sessionId !== 'string' || !sessionId || sessionId.length > 256) throw new Error(FAILURE);
  return createHash('sha256').update(JSON.stringify([config.baseUrl, config.clientId, sessionId])).digest('hex');
}

export async function readWorkflow(config, sessionId) {
  await checkedDirectory(config.stateDir);
  const key = stateKey(config, sessionId);
  const path = join(config.stateDir, `${key}.json`);
  try {
    const data = JSON.parse(privateFile(path));
    if (data.version !== 1 || data.key !== key || !TOKEN.test(data.workflowToken)) throw new Error(FAILURE);
    return data.workflowToken;
  } catch (error) {
    if (error.code === 'ENOENT') return undefined;
    throw new Error(FAILURE);
  }
}

export async function deleteWorkflow(config, sessionId) {
  await checkedDirectory(config.stateDir);
  const path = join(config.stateDir, `${stateKey(config, sessionId)}.json`);
  // Reject unsafe retained state before deleting; cleanup never follows a symlink.
  privateFile(path);
  await unlink(path);
}

export async function saveWorkflow(config, sessionId, workflowToken) {
  if (!TOKEN.test(workflowToken)) throw new Error(FAILURE);
  await checkedDirectory(config.stateDir);
  const key = stateKey(config, sessionId);
  const path = join(config.stateDir, `${key}.json`);
  const temporary = join(config.stateDir, `.${key}.${randomBytes(8).toString('hex')}.tmp`);
  let handle;
  try {
    handle = await open(temporary, 'wx', 0o600);
    await handle.writeFile(JSON.stringify({ version: 1, key, workflowToken }));
    await handle.sync();
    await handle.close();
    handle = undefined;
    await rename(temporary, path);
    const directory = await open(config.stateDir, 'r');
    try { await directory.sync(); } finally { await directory.close(); }
  } finally {
    await handle?.close();
    await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
  }
}
