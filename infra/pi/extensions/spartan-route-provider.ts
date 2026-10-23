import { getAgentDir, type ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { openAIResponsesApi } from '@earendil-works/pi-ai';
import { dirname, join } from 'node:path';
import { homedir } from 'node:os';
import { Discovery, loadConfig, gatewayPath, gatewayRequest } from './spartan-route-provider/discovery.mjs';
import { agentScope, createResponsesStream } from './spartan-route-provider/responses.mjs';

const shellQuote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;

export default async function (pi: ExtensionAPI) {
  const dataHome = process.env.XDG_DATA_HOME || join(homedir(), '.local/share');
  const configPath = process.env.PI_SPARTAN_ROUTE_CONFIG || join(dataHome, 'pi-spartan-route/config.json');
  const config = await loadConfig(configPath);
  const discovery = new Discovery(dirname(configPath), config);
  const scope = await agentScope(getAgentDir());
  const offline = process.argv.includes('--offline') || /^(1|true|yes)$/i.test(process.env.PI_OFFLINE ?? '');
  let models = await discovery.get({ network: false });
  let unavailable = false;
  if (!offline) {
    try { models = await discovery.get(); }
    catch { unavailable = true; }
  }
  pi.registerProvider('spartan-route', {
    name: 'Spartan Route', baseUrl: config.baseUrl, api: 'openai-responses',
    apiKey: 'gateway', authHeader: false,
    headers: { Authorization: `!python3 ${shellQuote(gatewayPath)} header`, 'X-Spartan-Pi-Client': '1' },
    models,
    streamSimple: createResponsesStream(openAIResponsesApi().streamSimple, gatewayRequest, scope,
      (id: string) => discovery.models.find((model: any) => model.id === id)?.spartanReasoningRealm),
    refreshModels: (context) => discovery.get({ network: context.allowNetwork, force: context.force, signal: context.signal }),
  });
  let timer: ReturnType<typeof setInterval> | undefined;
  let controller: AbortController | undefined;
  function stop() {
    if (timer) clearInterval(timer);
    timer = undefined;
    controller?.abort();
  }
  pi.on('session_start', (_event, ctx) => {
    stop();
    if (unavailable) ctx.ui.notify('Spartan Route unavailable; using the cached model catalog.', 'warning');
    if (!offline) {
      controller = new AbortController();
      const signal = controller.signal;
      timer = setInterval(() => {
        void Promise.resolve().then(() => ctx.modelRegistry.refresh({
          providers: ['spartan-route'], allowNetwork: true, force: true, signal,
        })).catch(() => {});
      }, config.refreshSeconds * 1000);
      timer.unref();
    }
  });
  pi.on('session_shutdown', stop);
  pi.registerCommand('spartan-route-refresh', {
    description: 'Refresh the Spartan Route model catalog',
    getArgumentCompletions: () => [],
    handler: async (_args, ctx) => {
      const result = await ctx.modelRegistry.refresh({ providers: ['spartan-route'], allowNetwork: true, force: true });
      ctx.ui.notify(result.errors.size ? 'Spartan Route catalog unavailable.' : `Spartan Route: ${discovery.models.length} models.`, result.errors.size ? 'warning' : 'info');
    },
  });
  pi.registerCommand('spartan-route-status', {
    description: 'Show Spartan Route model catalog status',
    getArgumentCompletions: () => [],
    handler: async (_args, ctx) => ctx.ui.notify(`Spartan Route: ${discovery.models.length} models; catalog ${discovery.source}.`, 'info'),
  });
}
