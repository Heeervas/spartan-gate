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
function quotaUnits(value) {
  if (!Number.isFinite(value) || value < 0) return '~?u';
  if (value === 0) return '0.00u';
  if (value < 0.001) return '<0.001u';
  return `~${value.toFixed(value < 0.01 ? 3 : 2)}u`;
}
function uniqueAccounts(accounts) {
  const unique = new Map();
  for (const account of accounts) {
    if (!unique.has(account.id)) unique.set(account.id, account);
  }
  return [...unique.values()];
}
function subsetSources(accounts, key, age, now) {
  return accounts.flatMap((account) => {
    const capacity = account.capacityMultiplier;
    const window = agedWindows(account, age)[key];
    if (!Number.isFinite(capacity) || capacity <= 0 || !window) return [];
    if (!Number.isFinite(Date.parse(window.updatedAt)) || !Number.isFinite(Date.parse(window.resetAt))) return [];
    const usable = percentage(window, now);
    if (!usable || window.remainingPercent < 0 || window.remainingPercent > 100) return [];
    const ageSeconds = window.ageSeconds ?? (now - Date.parse(window.updatedAt)) / 1000;
    return [{ window: { ...window, ageSeconds, stale: usable.stale }, capacity }];
  });
}
function subsetWindow(sources) {
  const capacity = sources.reduce((sum, source) => sum + source.capacity, 0);
  if (!Number.isFinite(capacity) || capacity <= 0) return null;
  const remaining = sources.reduce((sum, source) => sum + source.window.remainingPercent / 100 * source.capacity, 0);
  return { remainingPercent: remaining / capacity * 100,
    ageSeconds: Math.max(...sources.map(({ window }) => window.ageSeconds)),
    updatedAt: new Date(Math.min(...sources.map(({ window }) => Date.parse(window.updatedAt)))).toISOString(),
    resetAt: new Date(Math.min(...sources.map(({ window }) => Date.parse(window.resetAt)))).toISOString(),
    stale: sources.some(({ window }) => window.stale) };
}
function poolDisplay(document, windows, age, now) {
  const accounts = uniqueAccounts(document.accounts ?? []);
  const unresolved = (document.unresolvedSlots ?? []).length;
  const result = { windows: { ...windows }, partial: false, coverage: [] };
  for (const [key, label] of [['fiveHour', '5h'], ['weekly', 'weekly']]) {
    const sources = subsetSources(accounts, key, age, now);
    const capacity = sources.reduce((sum, source) => sum + source.capacity, 0);
    const unknown = accounts.length - sources.length + unresolved;
    result.coverage.push(`${label}: ${sources.length} known / ${unknown} unknown sources; known capacity ${compact(capacity)}x`);
    if (windows[key]?.remainingPercent !== null) continue;
    const subset = subsetWindow(sources);
    if (subset) { result.windows[key] = subset; result.partial = true; }
  }
  const unavailable = !percentage(windows.fiveHour, now) || !percentage(windows.weekly, now);
  const incomplete = Object.values(windows).some((window) => window?.capacityIncomplete);
  result.details = `${unavailable ? 'Full-pool percentage unavailable; ' : ''}${incomplete ? 'capacity incomplete; ' : ''}${result.coverage.join('\n')}`;
  return result;
}
function sessionCounters(session, fallback) {
  const totals = session.totals ?? fallback;
  return { totals, label: `${compact(totals.totalTokens)} tok ${quotaUnits(session.estimatedQuotaUnits)}` };
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
  const sourceWindows = agedWindows(scope.windows, age);
  const pool = view === 'lease' ? { windows: sourceWindows, partial: false, details: '' } : poolDisplay(document, sourceWindows, age, now);
  const windows = pool.windows;
  const counters = sessionCounters(session, fallback);
  const flags = `${offline ? ' offline' : ''}${document.session ? '' : ' native fallback'}${trackingLabel(session.coverage ?? {})}`;
  const status = `Route${scope.prefix} ${quotaFooter(windows, now)}${pool.partial ? ' partial' : ''} · ${counters.label}${flags}`;
  const rows = view === 'accounts' ? accountRows(document.accounts ?? [], age, now) : undefined;
  return { status, rows, details: usageDetails(status, windows, session, counters.totals, now) + `\n${pool.details}` };
}
