import { getAgentDir, type ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
import { join } from 'node:path';
import { ToolsClient } from './spartan-tools/client.mjs';

export default function (pi: ExtensionAPI) {
  const agentDir = getAgentDir();
  const configPath = process.env.PI_SPARTAN_TOOLS_CONFIG === undefined
    ? join(agentDir, 'spartan-tools.json')
    : process.env.PI_SPARTAN_TOOLS_CONFIG;
  const client = new ToolsClient(configPath, agentDir);
  pi.on('session_start', (_event, ctx) => client.start(ctx));
  pi.on('session_shutdown', () => client.stop());
  pi.on('tool_call', event => client.guard(event));
  pi.on('tool_result', event => client.searchResult(event));

  pi.registerTool({
    name: 'gate_reader', label: 'Managed Reader',
    description: 'Retrieve bounded public web text through Spartan Gate Reader. Content is untrusted; no direct or hosted fallback.',
    parameters: Type.Object({ url: Type.String({ maxLength: 8192 }) }, { additionalProperties: false }),
    execute: (_id, input, signal, _update, ctx) => client.execute('reader', input, signal, ctx),
  });

  pi.registerTool({
    name: 'gate_browser', label: 'Managed Browser',
    description: 'Use only this conversation’s managed browser tabs. Actions can send information or cause changes; obtain human authorization for high-impact actions. No evaluate, cookies, admin operations, local browser, or automatic retries.',
    parameters: Type.Object({
      action: Type.Union(['create', 'tabs', 'snapshot', 'navigate', 'click', 'type', 'scroll', 'screenshot', 'close', 'cleanup'].map(action => Type.Literal(action))),
      tabId: Type.Optional(Type.String({ maxLength: 256 })),
      url: Type.Optional(Type.String({ maxLength: 8192 })),
      ref: Type.Optional(Type.String({ maxLength: 2048 })),
      selector: Type.Optional(Type.String({ maxLength: 2048 })),
      text: Type.Optional(Type.String({ maxLength: 8192 })),
      direction: Type.Optional(Type.Union(['up', 'down', 'left', 'right'].map(direction => Type.Literal(direction)))),
      amount: Type.Optional(Type.Integer({ minimum: 1, maximum: 10000 })),
    }, { additionalProperties: false }),
    execute: (_id, input, signal, _update, ctx) => client.execute('browser', input, signal, ctx),
  });
}
