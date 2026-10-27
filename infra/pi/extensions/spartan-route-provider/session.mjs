import { createHash } from 'node:crypto';
import { routeSessionId } from '../spartan-route-usage/session.mjs';

export const LOCK = 'spartan-route-replay-realm-v2';
// Pi reload replaces extension modules/fences but retains its SessionManager.
// Keep poisoned-memory and provisional ownership with that manager, across module
// reloads as well. Only a newly opened manager can recover persisted disk state.
const STATE = Symbol.for('spartan-route.session-fence.v2');
const managers = globalThis[STATE] ??= new WeakMap();
const unavailable = () => new Error('Route session state unavailable; restart after repairing session storage');
const mismatch = () => new Error('Route reasoning realm mismatch; start a new session or fork');

export function replayIdentity(scope, sessionId) {
  const canonical = routeSessionId(sessionId);
  if (!canonical) throw new Error('Route session identity unavailable');
  return createHash('sha256').update(`pi-replay-v2\0${scope}\0${canonical}`).digest('hex');
}

// Local cooperative safety only. Server authorization never trusts this record.
export class SessionFence {
  constructor(scope, append) { this.scope = scope; this.append = append; }
  activate(ctx) { this.activation = { ctx }; }
  deactivate() { this.activation = undefined; }
  capture() {
    try {
      const activation = this.activation;
      const manager = activation?.ctx.sessionManager;
      const sessionId = manager?.getSessionId();
      const identity = replayIdentity(this.scope, sessionId);
      return { activation, manager, sessionId, identity };
    } catch { throw unavailable(); }
  }
  check(owner, realm) {
    const { activation, manager, sessionId, identity } = owner;
    try {
      // Read the guarded context again: a retained raw manager cannot authorize
      // work after native replacement/reload, even when its ID is unchanged.
      if (!activation || activation !== this.activation || activation.ctx.sessionManager !== manager ||
          manager.getSessionId() !== sessionId) throw unavailable();
    } catch { throw unavailable(); }
    if (!managers.has(manager)) managers.set(manager, { failed: new Set(), pending: new Map() });
    const state = managers.get(manager);
    if (state.failed.has(identity)) throw unavailable();
    const locks = manager.getEntries().filter(entry =>
      entry.type === 'custom' && entry.customType === LOCK && entry.data?.identity === identity);
    if (locks.some(entry => entry.data.version !== 2 || entry.data.realm !== realm)) throw mismatch();
    return { manager, state, locks };
  }
  reserve(owner, realm) {
    const { identity } = owner;
    const { manager, state } = this.check(owner, realm);
    let pending = state.pending.get(identity);
    if (pending && pending.realm !== realm) throw mismatch();
    if (!pending) state.pending.set(identity, pending = { realm, count: 0 });
    pending.count++;
    return { ...owner, realm, manager, state, pending, active: true };
  }
  commit(ticket) {
    const { manager, state, locks } = this.check(ticket, ticket.realm);
    if (!ticket.active || manager !== ticket.manager) throw unavailable();
    if (!locks.length) {
      try { this.append(LOCK, { version: 2, identity: ticket.identity, realm: ticket.realm }); }
      catch {
        // _appendEntry mutates memory before _persist. Every replacement fence
        // must reject this phantom lock, even if the disk is subsequently repaired.
        state.failed.add(ticket.identity);
        throw unavailable();
      }
    }
    return ticket.identity;
  }
  release(ticket) {
    if (!ticket.active) return;
    ticket.active = false;
    if (--ticket.pending.count === 0) ticket.state.pending.delete(ticket.identity);
  }
  claim(sessionId, realm) {
    const owner = this.capture();
    if (owner.sessionId !== sessionId) throw unavailable();
    const ticket = this.reserve(owner, realm);
    try { return this.commit(ticket); }
    finally { this.release(ticket); }
  }
}
