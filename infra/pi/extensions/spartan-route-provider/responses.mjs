import { createHash } from 'node:crypto';
import { realpath } from 'node:fs/promises';

const HEX = /^[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const REALMS = new Set(['codex_subscription', 'openai_api']);
const PRIVATE_PREFIX = '_spartan_';

export async function agentScope(agentDir) {
  return createHash('sha256').update(await realpath(agentDir)).digest('hex');
}

export function validateBinding(document, realm) {
  const binding = document?.binding;
  if (document?.version !== 1 || !binding || !HEX.test(binding.spartan_chain_id ?? '') ||
      !HEX.test(binding.spartan_binding_mac ?? '') || binding.spartan_expected_realm !== realm) {
    throw new Error('Route reasoning binding unavailable');
  }
  return {
    spartan_chain_id: binding.spartan_chain_id,
    spartan_expected_realm: binding.spartan_expected_realm,
    spartan_binding_mac: binding.spartan_binding_mac,
  };
}

function matchesBinding(item, binding) {
  return item._spartan_chain_id === binding.spartan_chain_id &&
    item._spartan_reasoning_realm === binding.spartan_expected_realm &&
    item._spartan_binding_mac === binding.spartan_binding_mac;
}

function publicItem(item) {
  return Object.fromEntries(Object.entries(item).filter(([key]) => !key.startsWith(PRIVATE_PREFIX)));
}

function replayItem(item, binding, reset) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) return item;
  if (item.type === 'reasoning' && !matchesBinding(item, binding)) return undefined;
  const cleaned = publicItem(item);
  if (reset && ['message', 'function_call', 'custom_tool_call'].includes(cleaned.type)) delete cleaned.id;
  return cleaned;
}

export function prepareRequest(payload, binding, sessionId) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('Invalid Route request');
  const input = payload.input;
  const reset = Array.isArray(input) && input.some(item =>
    item?.type === 'reasoning' && !matchesBinding(item, binding));
  const result = { ...payload, ...binding };
  // Native sessions replay complete transcripts. Never reference provider-side response state.
  delete result.previous_response_id;
  delete result.response_id;
  if (Array.isArray(input)) result.input = input.map(item => replayItem(item, binding, reset)).filter(item => item !== undefined);
  if (result.prompt_cache_key == null || (typeof result.prompt_cache_key === 'string' && !result.prompt_cache_key.trim())) {
    result.prompt_cache_key = `pi:${sessionId}`;
  }
  return result;
}

function stampItem(item, binding) {
  if (item?.type !== 'reasoning') return;
  item._spartan_chain_id = binding.spartan_chain_id;
  item._spartan_reasoning_realm = binding.spartan_expected_realm;
  item._spartan_binding_mac = binding.spartan_binding_mac;
}

export function stampEvent(event, binding) {
  if (!binding || !event || typeof event !== 'object') return;
  if (event.type === 'response.output_item.done') stampItem(event.item, binding);
  if (Array.isArray(event.response?.output)) {
    for (const item of event.response.output) stampItem(item, binding);
  }
}

// Delegate HTTP, retries, cancellation, SSE parsing and Pi messages to native Pi.
// The callbacks close over one request so concurrent calls cannot share provenance.
export function createResponsesStream(nativeStream, request, scope, realmForModel) {
  return (model, context, options = {}) => {
    let binding;
    return nativeStream(model, context, {
      ...options,
      onPayload: async (payload, requestModel) => {
        const changed = await options.onPayload?.(payload, requestModel);
        const actual = changed === undefined ? payload : changed;
        const realm = realmForModel(actual?.model);
        if (!REALMS.has(realm) || !UUID.test(options.sessionId ?? '')) throw new Error('Route session binding unavailable');
        options.signal?.throwIfAborted();
        binding = validateBinding(await request('binding', {
          scope: await scope, sessionId: options.sessionId, realm,
        }, options.signal), realm);
        options.signal?.throwIfAborted();
        return prepareRequest(actual, binding, options.sessionId);
      },
      onProviderStreamEvent: async (event, requestModel) => {
        await options.onProviderStreamEvent?.(event, requestModel);
        stampEvent(event, binding);
      },
    });
  };
}
