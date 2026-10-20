import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

// Run with Node 22 and an installed Pi; override its package directory if needed.
const require = createRequire(import.meta.url);
const piDir = process.env.PI_CODING_AGENT_DIR ||
  dirname(require.resolve("@earendil-works/pi-coding-agent/package.json"));
const { loadExtensions, clearExtensionCache } = await import(
  pathToFileURL(resolve(piDir, "dist/core/extensions/loader.js")).href
);
const extensionPath = fileURLToPath(new URL("./extensions/spartan-route-cache.ts", import.meta.url));
const sessionA = "a7becb62-8311-466d-89ca-e8b215d0a700";
const sessionB = "ac10e7cc-cdb6-4e29-9b15-7c8d87bdf5bd";
const model = { provider: "spartan-route", api: "openai-completions" };

async function loadHandler() {
  const { extensions, errors } = await loadExtensions([extensionPath], dirname(extensionPath));
  assert.deepEqual(errors, []);
  assert.equal(extensions.length, 1);
  assert.deepEqual([...extensions[0].handlers.keys()], ["before_provider_request"]);
  const handlers = extensions[0].handlers.get("before_provider_request");
  assert.equal(handlers.length, 1);
  return handlers[0];
}

function invoke(handler, payload, sessionId = sessionA, selectedModel = model) {
  return handler({ type: "before_provider_request", payload }, {
    model: selectedModel,
    sessionManager: { getSessionId: () => sessionId },
  });
}

test("session keys survive turns, reload and resume; new sessions get distinct keys", async () => {
  let handler = await loadHandler();
  let sessionId = sessionA;
  const ctx = { model, sessionManager: { getSessionId: () => sessionId } };
  const key = () => handler({ type: "before_provider_request", payload: {} }, ctx).prompt_cache_key;
  assert.equal(key(), `pi:${sessionA}`);
  assert.equal(key(), `pi:${sessionA}`);
  clearExtensionCache();
  handler = await loadHandler();
  assert.equal(key(), `pi:${sessionA}`);
  sessionId = sessionB;
  assert.equal(key(), `pi:${sessionB}`);
  sessionId = sessionA;
  assert.equal(key(), `pi:${sessionA}`);
  assert.ok(key().length <= 64);
});

test("missing and blank keys return a replacement without mutating payload", async () => {
  const handler = await loadHandler();
  for (const key of [undefined, null, "", " \t\n"]) {
    const payload = Object.freeze({ messages: [{ role: "user", content: "hello" }], prompt_cache_key: key });
    const result = invoke(handler, payload);
    assert.notEqual(result, payload);
    assert.deepEqual(result, { ...payload, prompt_cache_key: `pi:${sessionA}` });
    assert.equal(payload.prompt_cache_key, key);
    assert.equal(result.messages, payload.messages);
  }
});

test("explicit keys, other providers and other APIs are untouched", async () => {
  const handler = await loadHandler();
  for (const key of ["explicit", " explicit ", "x".repeat(80), 12, false, {}]) {
    const payload = Object.freeze({ prompt_cache_key: key });
    assert.equal(invoke(handler, payload), undefined);
    assert.equal(payload.prompt_cache_key, key);
  }
  for (const selectedModel of [undefined, { ...model, provider: "openai" }, { ...model, api: "openai-responses" }]) {
    const result = await handler({ type: "before_provider_request", payload: {} }, {
      model: selectedModel,
      sessionManager: { getSessionId() { throw new Error("must not read session"); } },
    });
    assert.equal(result, undefined);
  }
});

test("malformed payloads and session IDs never generate a shared fallback key", async () => {
  const handler = await loadHandler();
  for (const payload of [null, undefined, [], "payload", 0, true]) {
    assert.equal(invoke(handler, payload), undefined);
  }
  for (const sessionId of [null, undefined, "", " \t", 0, false, {}, "x".repeat(62)]) {
    const payload = Object.freeze({ messages: [] });
    assert.equal(handler({ type: "before_provider_request", payload }, {
      model, sessionManager: { getSessionId: () => sessionId },
    }), undefined);
    assert.deepEqual(payload, { messages: [] });
  }
  assert.equal(invoke(handler, {}, "x".repeat(61)).prompt_cache_key.length, 64);
});
