import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';

const require = createRequire(import.meta.url);
export const piDir = process.env.PI_CODING_AGENT_DIR || dirname(require.resolve('@earendil-works/pi-coding-agent/package.json'));

// Pi AI exposes import-only exports. require.resolve cannot resolve them; use
// Node's actual dependency search roots for the explicitly selected Pi package,
// supporting both npm-hoisted 1.3 and bundled/global 1.4 without installing.
export function piDependency(name, entry) {
  const roots = createRequire(resolve(piDir, 'package.json')).resolve.paths(name);
  const path = roots.map(root => join(root, name, entry)).find(path => existsSync(path));
  if (!path) throw new Error(`Missing native Pi test dependency: ${name}`);
  return path;
}
