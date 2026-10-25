import { getAgentDir, type ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { UsageClient } from './spartan-route-usage/client.mjs';
import { correlationHeaders } from './spartan-route-usage/correlation.mjs';

export default function (pi: ExtensionAPI) {
  const client = new UsageClient(getAgentDir());
  pi.on('session_start', (_event, ctx) => client.start(ctx));
  pi.on('session_shutdown', () => client.stop());
  pi.on('before_provider_headers', (event, ctx) => {
    correlationHeaders(event.headers, ctx);
    return client.headers(event.headers, ctx);
  });
  pi.on('model_select', (_event, ctx) => client.selected(ctx));
  pi.on('agent_end', (_event, ctx) => client.settled(ctx));
  pi.on('session_compact', (_event, ctx) => client.settled(ctx));
  pi.on('session_tree', (_event, ctx) => client.settled(ctx));
  pi.registerCommand('spartan-usage', {
    description: 'Route usage details; view pool|accounts|lease; refresh quota',
    handler: (args, ctx) => client.command(args, ctx),
  });
}
