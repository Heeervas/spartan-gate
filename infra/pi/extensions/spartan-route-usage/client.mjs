import { execFile } from 'node:child_process';
import { readFile, mkdir, writeFile, rename, unlink, access } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { nativeBaseline, presentation } from './core.mjs';
import { routeSessionId } from './session.mjs';

const gateway = fileURLToPath(new URL('./gateway.py', import.meta.url));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function gatewayRequest(action, document, signal) {
  return new Promise((resolve, reject) => {
    const child = execFile('python3', [gateway, action], {
      timeout: 11000, maxBuffer: 256 * 1024, signal,
    }, (error, stdout) => {
      // Process errors include stdout/stderr and private filesystem paths. Never surface them.
      if (error) return reject(new Error('Route usage unavailable'));
      try { resolve(JSON.parse(stdout)); } catch { reject(new Error('Route usage unavailable')); }
    });
    child.stdin.on('error', () => {});
    child.stdin.end(JSON.stringify(document));
  });
}

export class UsageClient {
  constructor(agentDir, request = gatewayRequest) {
    this.preferences = join(agentDir, 'spartan-route-usage.json');
    this.gapsDir = join(agentDir, 'spartan-route-usage-gaps');
    this.scheduleEpoch = 0;
    this.retryDelay = 60000;
    this.request = request;
    this.view = 'pool';
    this.startupOffline = process.argv.includes('--offline') || /^(1|true|yes)$/i.test(process.env.PI_OFFLINE ?? '');
    this.generation = 0;
    this.timers = new Set();
  }
  stop() {
    this.generation++;
    this.controller?.abort();
    this.clearTimers();
    this.ctx?.ui.setStatus('spartan-route-usage', undefined);
    this.ctx?.ui.setWidget('spartan-route-usage', undefined);
    this.ctx = undefined;
    this.pending = undefined;
    this.polling = undefined;
    this.refreshing = undefined;
    this.sessionId = undefined;
    this.rawSessionId = undefined;
    this.snapshot = undefined;
    this.originId = undefined;
    this.trackingIncomplete = false;
    this.gapPending = false;
    this.gapSaved = false;
    this.gapSaving = undefined;
    this.gapRetryAt = 0;
    this.nextRetryAt = 0;
    this.retryDelay = 60000;
  }
  async start(ctx) {
    this.stop();
    this.ctx = ctx;
    this.rawSessionId = ctx.sessionManager.getSessionId();
    this.sessionId = routeSessionId(this.rawSessionId);
    this.controller = new AbortController();
    this.offline = this.startupOffline;
    const generation = this.generation;
    try {
      const prefs = JSON.parse(await readFile(this.preferences, 'utf8'));
      if (['pool', 'accounts', 'lease'].includes(prefs.view)) this.view = prefs.view;
    } catch { /* Missing preferences use the native global default. */ }
    const gap = await this.loadGap(this.sessionId);
    if (generation !== this.generation) return;
    this.gapSaved = gap === true;
    this.gapPending = gap !== false;
    this.trackingIncomplete = gap !== false;
    if (!this.startupOffline && ctx.model?.provider === 'spartan-route') await this.ensure(ctx);
    this.render();
    this.schedule();
  }
  render() {
    if (!this.ctx?.hasUI) return;
    if (this.ctx.model?.provider !== 'spartan-route') {
      this.ctx.ui.setStatus('spartan-route-usage', undefined);
      this.ctx.ui.setWidget('spartan-route-usage', undefined);
      return;
    }
    let fallback;
    try { fallback = nativeBaseline(this.ctx.sessionManager.getEntries()); }
    catch { fallback = { totalTokens: 0, unknownQuotaTokens: 0 }; }
    const result = presentation(this.snapshot, this.view, fallback, this.offline);
    const status = result.status + (this.trackingIncomplete && !result.status.includes('incomplete') ? ' incomplete' : '');
    this.ctx.ui.setStatus('spartan-route-usage', this.ctx.ui.theme?.fg('accent', status) ?? status);
    this.ctx.ui.setWidget('spartan-route-usage', result.rows);
    return result;
  }
  gapFile(id) {
    if (!UUID.test(id ?? '')) throw new Error('Invalid session');
    return join(this.gapsDir, id);
  }
  async loadGap(id) {
    if (!UUID.test(id ?? '')) return false;
    try { await access(this.gapFile(id)); return true; }
    catch (error) { return error.code === 'ENOENT' ? false : null; }
  }
  async markGap(id) {
    this.trackingIncomplete = true;
    this.gapPending = true;
    if (this.gapSaved || Date.now() < this.gapRetryAt) return;
    if (this.gapSaving) return this.gapSaving;
    const generation = this.generation;
    this.gapRetryAt = Date.now() + 60000;
    const saving = (async () => {
      try {
        await mkdir(this.gapsDir, { recursive: true });
        await writeFile(this.gapFile(id), '', { flag: 'a', mode: 0o600 });
        if (generation === this.generation) this.gapSaved = true;
      } catch { /* Not saved: a crash may lose this gap. Retry is bounded. */ }
    })().finally(() => { if (this.gapSaving === saving) this.gapSaving = undefined; });
    this.gapSaving = saving;
    return saving;
  }
  async failed(generation) {
    if (generation !== this.generation) return;
    this.offline = true;
    this.nextRetryAt = Date.now() + this.retryDelay;
    this.retryDelay = Math.min(300000, this.retryDelay * 2);
    await this.markGap(this.sessionId);
  }
  async acknowledgeGap(session, id, generation) {
    if (generation !== this.generation || !session.coverage?.trackingIncomplete) return;
    try { await unlink(this.gapFile(id)); } catch (error) { if (error.code !== 'ENOENT') return; }
    if (generation === this.generation && this.sessionId === id) { this.gapPending = false; this.gapSaved = false; }
  }
  async recover(ctx, id, generation) {
    const signal = this.controller.signal;
    const recovered = await this.request('status', { sessionId: id }, signal);
    if (generation !== this.generation) return;
    if (recovered.status === 'unavailable') throw new Error('Usage unavailable');
    let session = recovered.session;
    if (this.originId && session?.originId !== this.originId) throw new Error('Origin unavailable');
    if (!session || this.gapPending) {
      const registered = await this.request('register', {
        sessionId: id, baseline: session ? session.baseline : nativeBaseline(ctx.sessionManager.getEntries()),
        trackingIncomplete: this.trackingIncomplete,
      }, signal);
      session = registered.session;
    }
    if (generation !== this.generation) return;
    if (session?.sessionId !== id || !UUID.test(session?.originId ?? '')) throw new Error('Invalid origin');
    await this.acknowledgeGap(session, id, generation);
    return { ...recovered, session, receivedAt: Date.now() };
  }
  async ensure(ctx) {
    if (!UUID.test(this.sessionId ?? '')) return;
    if (Date.now() < this.nextRetryAt) return this.originId;
    if (this.originId && !this.gapPending && !this.offline) return this.originId;
    if (this.pending) return this.pending;
    if (this.polling || this.refreshing) return (this.polling || this.refreshing).then(() => this.originId);
    const generation = this.generation;
    const pending = this.recover(ctx, this.sessionId, generation).then((snapshot) => {
      if (generation !== this.generation || !snapshot) return;
      this.originId = snapshot.session.originId;
      this.snapshot = snapshot;
      this.offline = false;
      this.nextRetryAt = 0;
      this.retryDelay = 60000;
      this.trackingIncomplete ||= !!snapshot.session.coverage?.trackingIncomplete;
      return this.originId;
    }).catch(async () => {
      await this.failed(generation);
    }).finally(() => {
      if (this.pending === pending) this.pending = undefined;
    });
    this.pending = pending;
    return pending;
  }
  async headers(headers, ctx) {
    const marker = Object.keys(headers).find((key) => key.toLowerCase() === 'x-spartan-pi-client');
    if (!marker || headers[marker] !== '1') return;
    for (const key of Object.keys(headers)) {
      if (['x-spartan-pi-session', 'x-spartan-pi-origin'].includes(key.toLowerCase())) delete headers[key];
    }
    if (ctx.sessionManager.getSessionId() !== this.rawSessionId) await this.start(ctx);
    const generation = this.generation;
    const origin = await this.ensure(ctx);
    if (generation !== this.generation) return;
    if (origin) {
      headers['X-Spartan-Pi-Session'] = this.sessionId;
      headers['X-Spartan-Pi-Origin'] = origin;
    } else {
      // Pi keeps inference available when telemetry fails. Persist the gap after recovery.
      await this.markGap(this.sessionId);
    }
    this.render();
    this.schedule();
  }
  async update(refresh = false) {
    const field = refresh ? 'refreshing' : 'polling';
    if (this.polling || this.refreshing || this.pending) return this.polling || this.refreshing || this.pending;
    if (!UUID.test(this.sessionId ?? '') || Date.now() < this.nextRetryAt) return;
    if (this.gapPending) return this.ensure(this.ctx);
    const generation = this.generation;
    const pending = (async () => {
      try {
        const document = { sessionId: this.sessionId };
        const signal = this.controller.signal;
        if (refresh) await this.request('refresh', document, signal);
        if (generation !== this.generation) return;
        const snapshot = await this.request('status', document, signal);
        if (generation !== this.generation) return;
        if (snapshot.status === 'unavailable') throw new Error('Usage unavailable');
        // A missing/corrupt server session cannot silently reset an already registered origin.
        if (this.originId && snapshot.session?.originId !== this.originId) throw new Error('Origin unavailable');
        this.snapshot = { ...snapshot, receivedAt: Date.now() };
        this.offline = false;
      } catch {
        await this.failed(generation);
      } finally {
        if (this[field] === pending) this[field] = undefined;
        if (generation === this.generation) this.render();
      }
    })();
    this[field] = pending;
    return pending;
  }
  clearTimers() {
    this.scheduleEpoch++;
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    this.scheduleMode = undefined;
  }
  automaticActive() {
    return this.ctx?.hasUI && !this.startupOffline && this.ctx.model?.provider === 'spartan-route' &&
      UUID.test(this.sessionId ?? '') && !this.controller.signal.aborted;
  }
  arm(delay, task, epoch) {
    const timer = setTimeout(async () => {
      this.timers.delete(timer);
      if (epoch !== this.scheduleEpoch || !this.automaticActive()) return;
      await task();
      if (epoch !== this.scheduleEpoch || !this.automaticActive()) return;
      if (this.offline || this.scheduleMode === 'retry') this.schedule();
      else this.arm(delay, task, epoch);
    }, delay);
    timer.unref?.();
    this.timers.add(timer);
  }
  async retry() {
    const generation = this.generation;
    await this.ensure(this.ctx);
    if (generation !== this.generation) return;
    if (this.originId) await this.update();
    if (generation !== this.generation) return;
  }
  schedule() {
    if (!this.automaticActive()) { this.clearTimers(); return; }
    const mode = this.offline ? 'retry' : 'online';
    if (mode === this.scheduleMode && this.timers.size) return;
    this.clearTimers();
    this.scheduleMode = mode;
    if (this.offline) this.arm(Math.max(0, this.nextRetryAt - Date.now()), () => this.retry(), this.scheduleEpoch);
    else {
      this.retryDelay = 60000;
      this.arm(30000, () => this.update(), this.scheduleEpoch);
      this.arm(60000 + Math.floor(Math.random() * 10000), () => this.update(true), this.scheduleEpoch);
    }
  }
  async selected(ctx) {
    this.clearTimers();
    this.ctx = ctx;
    const generation = this.generation;
    if (ctx.model?.provider === 'spartan-route' && !this.startupOffline) await this.ensure(ctx);
    if (generation !== this.generation) return;
    this.render();
    this.schedule();
  }
  async settled(ctx) {
    if (ctx.sessionManager.getSessionId() !== this.rawSessionId || ctx.model?.provider !== 'spartan-route') return;
    if (this.originId) await this.update();
    else this.render();
    this.schedule();
  }
  async command(args, ctx) {
    const parts = args.trim().split(/\s+/);
    if (parts[0] === 'view' && parts.length === 2 && ['pool', 'accounts', 'lease'].includes(parts[1])) {
      this.view = parts[1];
      try {
        await mkdir(dirname(this.preferences), { recursive: true });
        const temp = `${this.preferences}.${process.pid}.tmp`;
        await writeFile(temp, JSON.stringify({ view: this.view }) + '\n', { mode: 0o600 });
        await rename(temp, this.preferences);
      } catch { ctx.ui.notify('Route usage view changed; preference could not be saved.', 'warning'); }
      this.render();
    } else if (parts[0] === 'refresh' && parts.length === 1) {
      await this.ensure(ctx);
      if (!this.offline) await this.update(true);
      this.schedule();
      ctx.ui.notify(this.offline ? 'Route usage unavailable; native history retained.' : 'Route quota refresh requested (server due gate applies).', this.offline ? 'warning' : 'info');
    } else if (!args.trim()) {
      const result = this.render() ?? presentation(this.snapshot, this.view, nativeBaseline(ctx.sessionManager.getEntries()), this.offline);
      ctx.ui.notify(result.details, 'info');
    } else ctx.ui.notify('Usage: /spartan-usage [view pool|accounts|lease | refresh]', 'warning');
  }
}
