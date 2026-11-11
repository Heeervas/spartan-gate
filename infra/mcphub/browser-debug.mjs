/** Page-local diagnostics through Camofox's existing evaluate API, never DevTools. */
export const DEBUG_COVERAGE = 'Future main-page JS console/errors/fetch/XHR only; no prehistory, workers, all-frame traffic, browser-internal CORS/preflight or full Network trace.';

// Self-contained: serialized into the page; no backend identity or credential enters it.
function pageDebug(action, options) {
  const key = '__spartan_mcp_debug_v1__';
  const old = globalThis[key];
  if (action === 'read') return read(old);
  if (action === 'stop') {
    if (!old || old.id !== options.capture_id) return { ok: false, code: 'capture_missing' };
    old.stop(); delete globalThis[key]; return { ok: true };
  }
  if (old?.active) return { ok: false, code: 'capture_busy' };
  const capture = { id: options.capture_id, active: true, events: [], dropped: 0, bytes: 0, sequence: 0, restore: [] };
  const encoder = new TextEncoder();
  const started = Date.now();
  const pendingXHR = new Map();
  let pendingFetch = 0;
  let timer;
  function readable(value) {
    if (typeof value === 'string') return value.slice(0, 500);
    if (value === null || ['number', 'boolean', 'undefined'].includes(typeof value)) return String(value);
    return '[object omitted]'; // Never stringify page objects or invoke their getters.
  }
  function url(value) {
    try {
      if (typeof value !== 'string') return '[non-string URL]';
      const parsed = new URL(value, location.href);
      return parsed.origin + parsed.pathname.slice(0, 300); // No credentials/query/fragment.
    } catch { return '[invalid URL]'; }
  }
  function record(kind, data) {
    if (!capture.active) return;
    const event = { seq: ++capture.sequence, ms: Date.now() - started, kind, ...data };
    const bytes = encoder.encode(JSON.stringify(event)).length;
    if (capture.events.length >= 200 || capture.bytes + bytes > 65536) { capture.dropped++; return; }
    capture.events.push(event); capture.bytes += bytes;
  }
  function replace(object, name, wrapper) {
    const descriptor = Object.getOwnPropertyDescriptor(object, name);
    const original = object[name];
    const installed = wrapper(original);
    object[name] = installed;
    if (object[name] !== installed) throw new Error('capture hook is not writable');
    capture.restore.push(() => {
      if (object[name] !== installed) return; // Preserve later foreign replacements.
      if (descriptor) Object.defineProperty(object, name, descriptor);
      else delete object[name];
    });
  }
  function listen(name, callback) {
    addEventListener(name, callback);
    capture.restore.push(() => removeEventListener(name, callback));
  }
  function installConsole() {
    for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
      replace(console, level, original => function (...args) {
        try { record('console', { level, text: args.slice(0, 8).map(readable).join(' ').slice(0, 1000) }); } catch { /* Observer cannot break app. */ }
        return Reflect.apply(original, this, args);
      });
    }
    listen('error', event => { try { record('error', { text: readable(event.message) }); } catch { /* No app effect. */ } });
    listen('unhandledrejection', event => { try { record('rejection', { text: readable(event.reason) }); } catch { /* No app effect. */ } });
  }
  function requestInfo(input, init) {
    const request = typeof Request !== 'undefined' && input instanceof Request;
    return { method: String(init?.method || (request ? input.method : 'GET')).slice(0, 16), url: url(request ? input.url : input) };
  }
  function installFetch() {
    if (typeof globalThis.fetch !== 'function') return;
    replace(globalThis, 'fetch', original => function (...args) {
      const began = Date.now();
      let info;
      try { info = requestInfo(...args); record('fetch_start', info); } catch { info = { url: '[unavailable]', method: '?' }; }
      const result = Reflect.apply(original, this, args);
      // Bound pending observers, preserve promise identity and never consume bodies.
      if (pendingFetch >= 100) { capture.dropped++; return result; }
      pendingFetch++;
      Promise.resolve(result).then(response => {
        pendingFetch--;
        try { record('fetch_end', { ...info, status: response.status, duration_ms: Date.now() - began }); } catch { /* No app effect. */ }
      }, () => {
        pendingFetch--;
        try { record('fetch_error', { ...info, duration_ms: Date.now() - began }); } catch { /* No app effect. */ }
      });
      return result;
    });
  }
  function installXHR() {
    if (typeof XMLHttpRequest === 'undefined') return;
    const requests = new WeakMap();
    replace(XMLHttpRequest.prototype, 'open', original => function (method, target, ...rest) {
      try { requests.set(this, { method: readable(method).slice(0, 16), url: url(target) }); } catch { /* No app effect. */ }
      return Reflect.apply(original, this, [method, target, ...rest]);
    });
    replace(XMLHttpRequest.prototype, 'send', original => function (...args) {
      const info = requests.get(this) || { method: '?', url: '[unavailable]' };
      const began = Date.now();
      try {
        record('xhr_start', info);
        if (pendingXHR.size < 100 && !pendingXHR.has(this)) {
          const finished = () => {
            pendingXHR.delete(this);
            try { record('xhr_end', { ...info, status: this.status, duration_ms: Date.now() - began }); } catch { /* No app effect. */ }
          };
          pendingXHR.set(this, finished);
          this.addEventListener('loadend', finished, { once: true });
        } else capture.dropped++;
      } catch { /* No app effect. */ }
      return Reflect.apply(original, this, args);
    });
  }
  function stop() {
    capture.active = false; clearTimeout(timer);
    for (const restore of capture.restore.reverse()) { try { restore(); } catch { /* Foreign page state is not ours to repair. */ } }
    capture.restore = [];
    for (const [xhr, callback] of pendingXHR) { try { xhr.removeEventListener('loadend', callback); } catch { /* Page changed. */ } }
    pendingXHR.clear();
  }
  function read(value) {
    if (!value || value.id !== options.capture_id) return { ok: false, code: 'capture_missing' };
    const events = value.events.filter(event => event.seq > options.cursor).slice(0, options.limit);
    return { ok: true, capture_id: value.id, active: value.active, events, dropped: value.dropped,
      next_cursor: events.at(-1)?.seq ?? options.cursor };
  }
  capture.stop = stop;
  try { installConsole(); installFetch(); installXHR(); }
  catch { stop(); return { ok: false, code: 'capture_unsupported' }; }
  globalThis[key] = capture;
  timer = setTimeout(() => { stop(); capture.events = []; capture.bytes = 0; }, options.ttl_ms);
  return { ok: true, capture_id: capture.id };
}

export function debugScript(action, options) {
  return `(${pageDebug.toString()})(${JSON.stringify(action)},${JSON.stringify(options)})`;
}

function waitSelector(selector, timeoutMs) {
  return new Promise(resolve => {
    const deadline = Date.now() + timeoutMs;
    function poll() {
      try {
        if (document.querySelector(selector)) { resolve({ found: true }); return; }
      } catch { resolve({ found: false, invalid_selector: true }); return; }
      if (Date.now() >= deadline) { resolve({ found: false }); return; }
      setTimeout(poll, Math.min(50, Math.max(1, deadline - Date.now())));
    }
    poll();
  });
}

export const waitScript = (selector, timeout) => `(${waitSelector.toString()})(${JSON.stringify(selector)},${timeout})`;
