import { ServiceError } from './errors.mjs';

// Local Reader admission only. Gateway remains authoritative across processes
// and browser/search traffic. Reservations include response-body consumption.
export class ReaderAdmission {
  constructor({ timeoutMs = 30000 } = {}) {
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30000) throw new Error('Invalid Reader deadline');
    this.timeoutMs = timeoutMs;
    this.active = 0;
    this.pending = [];
  }

  async run(signal, operation) {
    const expires = performance.now() + this.timeoutMs;
    const deadline = AbortSignal.timeout(this.timeoutMs);
    const combined = AbortSignal.any([deadline, ...(signal ? [signal] : [])]);
    try {
      await this.acquire(combined, expires);
      try {
        combined.throwIfAborted();
        if (performance.now() >= expires) throw new ServiceError('deadline');
        const result = await operation(combined);
        combined.throwIfAborted();
        if (performance.now() >= expires) throw new ServiceError('deadline');
        return result;
      } finally { this.active--; this.drain(); }
    } catch (error) {
      if (combined.aborted) throw new ServiceError(deadline.aborted ? 'deadline' : 'cancelled');
      throw error;
    }
  }

  acquire(signal, expires) {
    signal.throwIfAborted();
    if (this.active < 2) { this.active++; return Promise.resolve(); }
    if (this.pending.length >= 16) throw new ServiceError('reader_queue_full');
    return new Promise((resolve, reject) => {
      const entry = { resolve, reject, signal, expires, abort: undefined };
      entry.abort = () => {
        const index = this.pending.indexOf(entry);
        if (index !== -1) this.pending.splice(index, 1);
        reject(signal.reason);
      };
      signal.addEventListener('abort', entry.abort, { once: true });
      this.pending.push(entry);
    });
  }

  drain() {
    while (this.active < 2 && this.pending.length) {
      const entry = this.pending.shift();
      entry.signal.removeEventListener('abort', entry.abort);
      if (entry.signal.aborted) { entry.abort(); continue; }
      if (performance.now() >= entry.expires) { entry.reject(new ServiceError('deadline')); continue; }
      this.active++;
      entry.resolve();
    }
  }
}
