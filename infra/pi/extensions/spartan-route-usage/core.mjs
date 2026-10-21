// Pi reports uncached input separately; Route includes cache reads/writes in input.
const counter = (value) => Number.isSafeInteger(value) && value >= 0 ? value : 0;
export function nativeBaseline(entries) {
  const total = { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0, totalTokens: 0, codexTokens: 0, unknownQuotaTokens: 0 };
  for (const entry of entries) {
    const row = entry.type === 'message' ? entry.message : entry;
    if (!row || !row.usage || !['assistant', 'toolResult', 'usage', 'compaction', 'branch_summary'].includes(row.role ?? row.type)) continue;
    const usage = row.usage;
    const input = counter(usage.input) + counter(usage.cacheRead) + counter(usage.cacheWrite);
    const output = counter(usage.output);
    total.inputTokens += input;
    total.cachedInputTokens += counter(usage.cacheRead);
    total.outputTokens += output;
    total.totalTokens += input + output;
    const codex = ['openai-codex', 'codex'].includes(row.provider) ||
      (row.provider === 'spartan-route' && typeof row.model === 'string' && row.model.startsWith('codex/'));
    if (codex) total.codexTokens += input + output;
    else if (!row.provider || !row.model) total.unknownQuotaTokens += input + output;
  }
  if (!Object.values(total).every(Number.isSafeInteger)) throw new Error('Native usage exceeds safe range');
  return total;
}

export const compact = (value) => new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value ?? 0);
const safeText = (text, limit = 60) => String(text ?? '').replace(/[\x00-\x1f\x7f-\x9f]/g, '').slice(0, limit);
function percentage(window, now) {
  if (!window) return null;
  if (Date.parse(window.resetAt) <= now) return null;
  const age = window.ageSeconds ?? (now - Date.parse(window.updatedAt)) / 1000;
  if (!Number.isFinite(age) || age > 3600 || !Number.isFinite(window.remainingPercent)) return null;
  return { value: Math.max(0, Math.min(100, window.remainingPercent)), stale: age > 120 || window.stale };
}
function barCells(top, bottom) {
  return Array.from({ length: 8 }, (_, index) => {
    const a = top && top.value >= (index + 0.5) * 12.5;
    const b = bottom && bottom.value >= (index + 0.5) * 12.5;
    return a ? (b ? '█' : '▀') : (b ? '▄' : '·');
  }).join('');
}
const percentLabel = (window) => window ? `${Math.round(window.value)}%${window.stale ? '~' : ''}` : '?';
export function dualBar(windows, now = Date.now()) {
  const top = percentage(windows?.fiveHour, now), bottom = percentage(windows?.weekly, now);
  return `[${barCells(top, bottom)}] 5h ${percentLabel(top)} wk ${percentLabel(bottom)}`;
}
export function resetCountdown(resetAt, now = Date.now()) {
  const minutes = Math.max(0, Math.ceil((Date.parse(resetAt) - now) / 60000));
  if (!Number.isFinite(minutes)) return '?';
  if (minutes >= 1440) return `${Math.floor(minutes / 1440)}d${Math.floor(minutes % 1440 / 60)}h`;
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h${minutes % 60}m`;
  return `${minutes}m`;
}
function agedWindows(windows = {}, age) {
  const result = {};
  for (const key of ['fiveHour', 'weekly']) {
    const window = windows[key];
    result[key] = window ? { ...window, ageSeconds: window.ageSeconds == null ? null : window.ageSeconds + age } : null;
  }
  return result;
}
function scopeView(snapshot, view) {
  if (view !== 'lease') return { windows: snapshot.pool, prefix: '' };
  const lease = snapshot.lease ?? {};
  const account = (snapshot.accounts ?? []).find((row) => row.slotIndexes.includes(lease.slotIndex));
  return { windows: account, prefix: ` lease ${safeText(lease.scope)} ${safeText(lease.state ?? 'unavailable')}` };
}
function quotaFooter(windows, now) {
  const top = percentage(windows.fiveHour, now), bottom = percentage(windows.weekly, now);
  if (!top && !bottom) return 'quota n/a';
  if (!top) return `wk ${percentLabel(bottom)} reset ${resetCountdown(windows.weekly.resetAt, now)}`;
  if (!bottom) return `5h ${percentLabel(top)} reset ${resetCountdown(windows.fiveHour.resetAt, now)}`;
  const stale = top.stale || bottom.stale ? '~' : '';
  return `[${barCells(top, bottom)}]${stale} 5h:${resetCountdown(windows.fiveHour.resetAt, now)} wk:${resetCountdown(windows.weekly.resetAt, now)}`;
}
function sessionCounters(session, fallback) {
  const totals = session.totals ?? fallback;
  const units = Number.isFinite(session.estimatedQuotaUnits) ? session.estimatedQuotaUnits.toFixed(2) : '?';
  return { totals, label: `${compact(totals.totalTokens)} tok ~${units}u` };
}
function trackingLabel(coverage) {
  return coverage.trackingIncomplete || coverage.usageMissingAttempts || coverage.incompleteAttempts ? ' incomplete' : '';
}
function accountRows(accounts, age, now) {
  return accounts.map((account) => `${safeText(account.id)} ${safeText(account.state ?? 'unknown')} ${dualBar(agedWindows(account, age), now)}`);
}
function usageDetails(status, windows, session, totals, now) {
  const baseline = session.baseline ?? totals;
  return `${status}\nRemaining (upper 5h / lower weekly): ${dualBar(windows, now)}\nHistorical quota-unclassified tokens: ${compact(baseline.unknownQuotaTokens)}\nQuota units are a calibrated estimate, not a measured quota debit.\nInput (cache inclusive): ${compact(totals.inputTokens)}; cached: ${compact(totals.cachedInputTokens)}; output: ${compact(totals.outputTokens)}\nCoverage: ${safeText(JSON.stringify(session.coverage ?? {}), 1000)}`;
}
export function presentation(snapshot, view, fallback, offline = false, now = Date.now()) {
  const document = snapshot ?? {};
  const session = document.session ?? {};
  const scope = scopeView(document, view);
  const age = Math.max(0, (now - (document.receivedAt ?? now)) / 1000);
  const windows = agedWindows(scope.windows, age);
  const counters = sessionCounters(session, fallback);
  const flags = `${offline ? ' offline' : ''}${document.session ? '' : ' native fallback'}${trackingLabel(session.coverage ?? {})}`;
  const status = `Route${scope.prefix} ${quotaFooter(windows, now)} · ${counters.label}${flags}`;
  const rows = view === 'accounts' ? accountRows(document.accounts ?? [], age, now) : undefined;
  return { status, rows, details: usageDetails(status, windows, session, counters.totals, now) };
}
