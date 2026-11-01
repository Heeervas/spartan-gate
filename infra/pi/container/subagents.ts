// One native entry for host and Hermes. Mutable upstream state never lives in
// this read-only program checkout. Loaded only in new, reviewed Pi processes.
import { getAgentDir, type ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

export default async function (pi: ExtensionAPI) {
  process.env.PI_SUBAGENTS_TEMP_ROOT = join(getAgentDir(), 'subagents/runtime');
  const root = process.env.PI_SUBAGENTS_PACKAGE_ROOT || join(homedir(), '.local/share/pi-subagents-nicobailon');
  const { default: register } = await import(pathToFileURL(join(root, 'index.ts')).href);
  await register(pi);
}
