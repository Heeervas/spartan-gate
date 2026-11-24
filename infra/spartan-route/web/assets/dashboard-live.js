/* Bounded RAM snapshots only; never render request content or infer missing usage. */
window.SpartanRouteLive = (() => {
  const number = value => Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString() : 'unknown';
  const ratio = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? `${value.toFixed(1)}%` : 'unknown';
  const metric = (label, value, detail) => `<div class="metric" title="${detail}"><div class="muted">${label}</div><div class="value">${value}</div><div class="muted">${detail}</div></div>`;
  function cacheRing(value) {
    const known = typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100;
    return `<svg class="ring-glyph" viewBox="0 0 100 100" width="42" height="42" role="img" aria-label="Cache reuse ${known ? ratio(value) : 'unknown'}"><circle class="glyph-track${known ? '' : ' glyph-unknown'}" cx="50" cy="50" r="44" fill="none" stroke="var(--control)" stroke-width="8"${known ? '' : ' stroke-dasharray="2 3"'}/>${known ? `<circle class="glyph-arc" cx="50" cy="50" r="44" fill="none" stroke="var(--good)" stroke-width="8" stroke-linecap="round" pathLength="100" stroke-dasharray="${value} 100" transform="rotate(-90 50 50)"/>` : ''}</svg>`;
  }
  function clear(metrics, status) {
    metrics.replaceChildren();
    delete metrics.dataset.observed;
    status.textContent = 'Live telemetry not loaded.';
  }
  function validSnapshot(payload) {
    return payload?.version === 1 && payload.source === 'memory' && payload.metrics && typeof payload.historicalTelemetryEnabled === 'boolean';
  }
  function durationValue(value) {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? `${(value / 1000).toFixed(2)} s` : 'unknown';
  }
  function startedLabel(value) {
    const time = typeof value === 'string' ? Date.parse(value) : NaN;
    return Number.isFinite(time) ? new Date(time).toLocaleString() : 'the latest restart';
  }
  function snapshotStatus(payload) {
    const age = payload.lastObservedAgeSeconds;
    const freshness = typeof age === 'number' && Number.isFinite(age) && age >= 0
      ? `Last observation ${Math.floor(age)} s ago` : 'Waiting for a finalized request';
    return `${freshness} · RAM only since ${startedLabel(payload.startedAt)}; resets on restart · all models, unfiltered${payload.saturated ? ' · counter limit reached; ratios unavailable' : ''}${payload.observationFailures ? ' · some observations unavailable' : ''}`;
  }
  function render(metrics, status, payload) {
    if (!validSnapshot(payload)) {
      throw new TypeError('Live telemetry unavailable: invalid snapshot');
    }
    const m = payload.metrics;
    const coverage = `${number(m.cacheKnownRequests)}/${number(m.requests)} with confirmed cache counters`;
    const usage = `${number(m.usageKnownRequests)}/${number(m.requests)} usage-reported · may be partial`;
    const duration = durationValue(m.averageDurationMs);
    metrics.innerHTML = [
      metric('Cache reuse', `<span class="live-cache-value">${cacheRing(payload.saturated ? null : m.cacheHitPercent)}<span>${payload.saturated ? 'unknown' : ratio(m.cacheHitPercent)}</span></span>`, `Token-weighted · ${coverage}`),
      metric('Requests · 15 min', number(m.requests), 'Finalized Chat / Responses'),
      metric('Input tokens', m.usageKnownRequests ? number(m.inputTokens) : 'unknown', usage),
      metric('Output tokens', m.usageKnownRequests ? number(m.outputTokens) : 'unknown', usage),
      metric('Request errors', number(m.errors), 'Not client disconnects alone'),
      metric('Mean request duration', payload.saturated ? 'unknown' : duration, 'Includes delivery; not generation speed'),
    ].join('');
    metrics.dataset.observed = 'true';
    status.textContent = snapshotStatus(payload);
    return payload.historicalTelemetryEnabled;
  }
  function unavailable(metrics, status, denied) {
    if (denied) clear(metrics, status);
    status.textContent = denied ? 'Live telemetry unavailable: check dashboard access.'
      : `Live refresh failed${metrics.dataset.observed ? ' · showing previous snapshot, not refreshed' : ' · no snapshot available'}`;
  }
  return {render, clear, unavailable};
})();
