import { createHash } from 'node:crypto';
import { realpath } from 'node:fs/promises';
import { routeSessionId } from '../spartan-route-usage/session.mjs';

const HEX = /^[0-9a-f]{64}$/;
const REALMS = new Set(['codex_subscription', 'openai_api']);
const FIELDS = ['spartan_chain_id', 'spartan_expected_realm', 'spartan_binding_mac'];
const BINDING_ERRORS = new Set(['reasoning_binding_invalid', 'reasoning_binding_unavailable', 'reasoning_realm_mismatch']);

export async function agentScope(agentDir) {
  return createHash('sha256').update(await realpath(agentDir)).digest('hex');
}

export function validateBinding(document, realm) {
  const binding = document?.binding;
  if (document?.error) {
    const code = BINDING_ERRORS.has(document.error.code) ? document.error.code : 'reasoning_binding_unavailable';
    throw new Error(`Route legacy migration: ${code}`);
  }
  if (document?.version !== 1 || !binding || !HEX.test(binding.spartan_chain_id ?? '') ||
      !HEX.test(binding.spartan_binding_mac ?? '') || binding.spartan_expected_realm !== realm) {
    throw new Error('Route reasoning binding unavailable');
  }
  return Object.fromEntries(FIELDS.map(key => [key, binding[key]]));
}

function matchesLegacy(item, binding) {
  return binding && item._spartan_chain_id === binding.spartan_chain_id &&
    item._spartan_reasoning_realm === binding.spartan_expected_realm &&
    item._spartan_binding_mac === binding.spartan_binding_mac;
}

function matchesLocal(item, identity, realm) {
  return item._spartan_replay_version === 2 && item._spartan_replay_identity === identity &&
    item._spartan_reasoning_realm === realm;
}

function publicItem(item) {
  const cleaned = Object.fromEntries(Object.entries(item).filter(([key]) => !key.startsWith('_spartan_')));
  if (['message', 'function_call', 'custom_tool_call'].includes(cleaned.type)) delete cleaned.id;
  return cleaned;
}

export function prepareRequest(payload, identity, realm, sessionId, binding) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('Invalid Route request');
  const result = { ...payload };
  for (const field of [...FIELDS, 'previous_response_id', 'response_id']) delete result[field];
  let legacy = false;
  if (Array.isArray(payload.input)) result.input = payload.input.flatMap(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return [item];
    if (item.type === 'reasoning' && !matchesLocal(item, identity, realm)) {
      if (!matchesLegacy(item, binding)) return [];
      legacy = true;
    }
    return [publicItem(item)];
  });
  if (legacy) Object.assign(result, binding);
  if (result.prompt_cache_key == null || (typeof result.prompt_cache_key === 'string' && !result.prompt_cache_key.trim())) {
    result.prompt_cache_key = `pi:${sessionId}`;
  }
  return result;
}

export function stampEvent(event, identity, realm, binding) {
  if (!event || typeof event !== 'object') return;
  const items = [event.item, ...(event.response?.output ?? [])];
  for (const item of items) {
    if (item?.type !== 'reasoning') continue;
    item._spartan_reasoning_realm = realm;
    if (binding) {
      item._spartan_chain_id = binding.spartan_chain_id;
      item._spartan_binding_mac = binding.spartan_binding_mac;
    } else {
      item._spartan_replay_version = 2;
      item._spartan_replay_identity = identity;
    }
  }
}

function legacyItems(payload) {
  return Array.isArray(payload?.input) && payload.input.some(item =>
    item?.type === 'reasoning' && item.encrypted_content && HEX.test(item._spartan_chain_id ?? '') &&
    HEX.test(item._spartan_binding_mac ?? ''));
}

function legacyHistory(context) {
  return context.messages?.some(message => Array.isArray(message.content) && message.content.some(block => {
    if (block.type !== 'thinking' || !block.thinkingSignature) return false;
    try { return legacyItems({ input: [JSON.parse(block.thinkingSignature)] }); }
    catch { return false; } // Native conversion still owns malformed signature rejection.
  }));
}

// Native Pi owns conversion/HTTP/SSE/cancellation. Only old raw histories use the
// compatibility binding endpoint; successful bindings live in this runtime only.
export function createResponsesStream(nativeStream, request, scope, realmForModel, fence) {
  const bindings = new Map();
  async function legacyBinding(sessionId, realm, signal) {
    const key = `${sessionId}\0${realm}`;
    if (!bindings.has(key)) {
      const pending = request('binding', { scope, sessionId, realm }, signal)
        .then(document => validateBinding(document, realm));
      bindings.set(key, pending);
      pending.catch(() => { if (bindings.get(key) === pending) bindings.delete(key); });
    }
    return bindings.get(key);
  }
  return (model, context, options = {}) => {
    let identity, realm, binding, owner, ownershipError;
    // Capture native ownership before native conversion or caller callbacks can
    // await. options.sessionId remains an independent provider routing/cache ID.
    try { owner = fence.capture(); }
    catch (error) { ownershipError = error; }
    return nativeStream(model, context, {
      ...options,
      onPayload: async (payload, requestModel) => {
        if (ownershipError) throw ownershipError;
        const changed = await options.onPayload?.(payload, requestModel);
        const actual = changed === undefined ? payload : changed;
        realm = realmForModel(actual?.model);
        if (!REALMS.has(realm)) throw new Error('Route reasoning realm unavailable');
        options.signal?.throwIfAborted();
        const ticket = fence.reserve(owner, realm);
        try {
          // Native cross-model conversion may already have omitted old reasoning.
          // Its saved provenance still needs validation before a durable realm lock.
          if (legacyItems(actual) || legacyHistory(context)) {
            binding = await legacyBinding(routeSessionId(owner.sessionId), realm, options.signal);
          }
          options.signal?.throwIfAborted();
          const prepared = prepareRequest(actual, ticket.identity, realm, options.sessionId, binding);
          identity = fence.commit(ticket);
          // A foreign legacy history may have been entirely removed.
          if (!prepared.spartan_binding_mac) binding = undefined;
          return prepared;
        } finally { fence.release(ticket); }
      },
      onProviderStreamEvent: async (event, requestModel) => {
        await options.onProviderStreamEvent?.(event, requestModel);
        stampEvent(event, identity, realm, binding);
      },
    });
  };
}
