    const tokenInput = document.getElementById('token');
    const metricsEl = document.getElementById('metrics');
    const sessionsEl = document.getElementById('sessions');
    const statusEl = document.getElementById('status');
    const filterStatusEl = document.getElementById('filter-status');
    const modelFilter = document.getElementById('model-filter');
    const reasoningFilter = document.getElementById('reasoning-filter');
    const promptFilter = document.getElementById('prompt-filter');
    const initialQuery = new URLSearchParams(location.search);
    let activeLoadController = null;
    let loadGeneration = 0;
    let promptTimer = null;
    tokenInput.value = sessionStorage.getItem('spartanRouteToken') || '';
    modelFilter.dataset.pending = initialQuery.get('model') || '';
    reasoningFilter.dataset.pending = initialQuery.get('reasoning') || '';
    promptFilter.value = initialQuery.get('prompt') || '';

    document.getElementById('save').onclick = () => {
      sessionStorage.setItem('spartanRouteToken', tokenInput.value.trim());
      load();
    };
    document.getElementById('refresh').onclick = () => load();
    document.getElementById('collapse').onclick = () => {
      sessionsEl.querySelectorAll('details[open]').forEach(item => { item.open = false; });
    };
    function selectedFilters() {
      return {
        model: modelFilter.value || modelFilter.dataset.pending || '',
        reasoning: reasoningFilter.value || reasoningFilter.dataset.pending || '',
        prompt: promptFilter.value.trim(),
      };
    }
    function syncFilterUrl(filters) {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
      history.replaceState(null, '', `${location.pathname}${query.size ? `?${query}` : ''}`);
    }
    function triggerFilterLoad() {
      modelFilter.dataset.pending = '';
      reasoningFilter.dataset.pending = '';
      syncFilterUrl(selectedFilters());
      load();
    }
    modelFilter.addEventListener('change', triggerFilterLoad);
    reasoningFilter.addEventListener('change', triggerFilterLoad);
    promptFilter.addEventListener('input', () => {
      clearTimeout(promptTimer);
      promptTimer = setTimeout(triggerFilterLoad, 300);
    });
    document.getElementById('clear-filters').onclick = () => {
      modelFilter.value = '';
      reasoningFilter.value = '';
      promptFilter.value = '';
      triggerFilterLoad();
    };

    function headers(json = false) {
      const token = tokenInput.value.trim();
      const out = token ? { Authorization: `Bearer ${token}` } : {};
      if (json) out['Content-Type'] = 'application/json';
      return out;
    }
    function esc(value) {
      return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c]));
    }
    function num(value) {
      return Number(value || 0).toLocaleString();
    }
    function pct(value) {
      return `${Number(value || 0).toFixed(1)}%`;
    }
    function tokenCachePercent(cached, input) {
      const inputTokens = Number(input || 0);
      return inputTokens > 0 ? `${((Number(cached || 0) / inputTokens) * 100).toFixed(1)}%` : '0.0%';
    }
    function fmtTime(value) {
      if (!value) return '-';
      const d = new Date(value);
      return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
    }
    function metric(label, value, extra = '') {
      return `<div class="metric"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="muted">${esc(extra)}</div></div>`;
    }
    function cacheUsagePercent(input, cached, native = {}) {
      const missing = Number(native.cacheReadMissingRequests || 0);
      const denominator = Number(input || 0) - (missing ? Number(native.inputTokens || 0) - Number(native.cacheReadInputTokens || 0) : 0);
      if (denominator <= 0) return 'unknown';
      return `${(100 * Number(cached || 0) / denominator).toFixed(1)}%${missing ? ' · partial' : ''}`;
    }
    function nativeCacheSummary(native = {}) {
      if (!native.requests) return 'no native comparisons yet';
      const comparisons = native.comparisons || {};
      const compatible = Number(comparisons.replay || 0) + Number(comparisons.append || 0);
      const unknown = Number(comparisons.unknown || 0) + Number(comparisons.unattributed || 0);
      return `${native.requests} native checks · ${compatible} compatible · ${Number(comparisons.changed || 0)} changed · ${unknown} unknown · cache data ${Number(native.cacheReadKnownRequests || 0)}/${Number(native.usageRequests || 0)}`;
    }
    function nativeCacheDetail(native) {
      if (!native || native.version !== 2) return 'Legacy cache heuristic; native request comparison unavailable';
      const labels = {replay: 'same structured request', append: 'history extended', changed: 'request changed',
        no_predecessor: 'no completed predecessor for this cache key', unattributed: 'comparison scope unavailable', unknown: 'comparison unknown'};
      const parts = [labels[native.comparison] || labels.unknown];
      if (native.changed_components?.length) parts.push(native.changed_components.join(', '));
      const conversation = native.conversation_trace;
      if (conversation?.comparison === 'continued') {
        parts.push('conversation continued');
        if (conversation.cache_key_changed === true) parts.push('cache key changed');
        if (conversation.changed_components?.length) parts.push(conversation.changed_components.map(name => `${name} changed`).join(', '));
      } else if (conversation?.comparison === 'unknown') {
        parts.push('conversation comparison unavailable');
      }
      if (conversation?.overlap === true) parts.push('overlapping conversation requests');
      if (native.overlap) parts.push('overlapping requests');
      if (native.predecessor_gap_ms != null) parts.push(`predecessor completed ${Math.round(native.predecessor_gap_ms / 1000)}s before send`);
      parts.push(native.cache_write_tokens == null ? 'cache writes unknown' : `${native.cache_write_tokens} cache-write tokens`);
      if (native.ttft_ms != null) parts.push(`${native.ttft_ms}ms to first output`);
      if (native.provider?.type) parts.push(`provider: ${native.provider.type}`);
      return parts.join(' · ');
    }
    function cacheLowSummary(low, likely) {
      return likely ? `${num(low)}/${num(likely)} low in legacy estimate` : '';
    }
    function likelyUsageCacheText(metrics = {}) {
      const likely = Number(metrics.cacheLikelyRequests || 0);
      const input = Number(metrics.cacheLikelyInputTokens || 0);
      if (!likely && !input) return '';
      const percent = metrics.cacheLikelyHitPercent !== undefined
        ? pct(metrics.cacheLikelyHitPercent)
        : tokenCachePercent(metrics.cacheLikelyCachedInputTokens, input);
      return `${percent} legacy likely cache`;
    }
    function fact(label, value, tone = '') {
      return `<span class="fact ${tone}"><span class="fact-label">${esc(label)}</span><strong>${esc(value)}</strong></span>`;
    }
    function reasoningSummary(metrics = {}) {
      const efforts = metrics.reasoningEfforts || {};
      const entries = Object.entries(efforts);
      return entries.length ? entries.map(([effort, count]) => `${effort} · ${count}`).join(', ') : 'default';
    }
    function toolSummary(metrics = {}) {
      const known = Number(metrics.toolTelemetryKnownRequests || 0);
      const partial = Number(metrics.toolTelemetryPartialRequests || 0);
      const unknown = Number(metrics.toolTelemetryUnknownRequests || 0);
      if (partial || unknown) {
        const calls = Number(metrics.actualToolCalls || 0);
        return calls > 0 ? `${calls}+ calls · partial` : 'unknown';
      }
      return known ? `${Number(metrics.actualToolCalls || 0)} calls` : 'unknown';
    }
    function usageUnitSummary(metrics = {}) {
      if (metrics.estimatedUsageUnits == null) return 'unknown';
      const units = Number(metrics.estimatedUsageUnits);
      if (!Number.isFinite(units)) return 'unknown';
      const value = units < 0.1 ? units.toFixed(3) : (units < 10 ? units.toFixed(2) : units.toFixed(1));
      return `~${value}u · pool est.`;
    }
    function metricLine(metrics = {}, models = []) {
      const cache = Number(metrics.usageSeenRequests || 0) ? cacheUsagePercent(metrics.inputTokens, metrics.cachedInputTokens, metrics.nativeCache) : 'unknown';
      const errors = Number(metrics.errors || 0);
      return [
        fact('Model', models.join(', ') || 'unknown'),
        fact('Cache', cache),
        fact('Cache checks', nativeCacheSummary(metrics.nativeCache)),
        fact('Est. usage', usageUnitSummary(metrics)),
        fact('Reasoning', reasoningSummary(metrics)),
        fact('Tools', toolSummary(metrics)),
        fact('Status', errors ? `${errors} errors` : 'OK', errors ? 'bad' : ''),
      ].join('');
    }
    function activityTitle(item, fallback) {
      const preview = String(item?.promptPreview || '').trim();
      return preview || fallback;
    }
    function requestPreview(row) {
      const preview = row.promptPreview || tracePreview(row);
      return preview && preview !== 'No preview' ? preview : 'No user-message preview recorded';
    }
    function tracePreview(row) {
      const items = row.context?.request_trace?.delta?.items || [];
      if (!Array.isArray(items) || items.length === 0) {
        return row.promptPreview || 'No preview';
      }
      return items.map(item => {
        const role = item.role || 'unknown';
        if (Array.isArray(item.toolCalls) && item.toolCalls.length) {
          const calls = item.toolCalls.map(call => `${call.name || 'tool'}(${(call.argumentKeys || []).join(', ') || `${call.argumentChars || 0} chars`})`).join('; ');
          return `${role}: ${calls}`;
        }
        return `${role}: ${item.preview || `${item.chars || 0} chars`}`;
      }).join('\n');
    }
    function renderTraceItems(row) {
      const items = row.context?.request_trace?.delta?.items || [];
      if (!Array.isArray(items) || items.length === 0) {
        return '<div class="trace-items"><div class="trace-item">No recorded trace items.</div></div>';
      }
      return `<div class="trace-items">${items.map(item => {
        const tools = (item.toolCalls || []).map(call => {
          const keys = (call.argumentKeys || []).join(', ') || `${call.argumentChars || 0} chars`;
          return `<div class="trace-tool">tool ${esc(call.name || 'tool')}(${esc(keys)})</div>`;
        }).join('');
        return `<div class="trace-item"><span class="trace-role">${esc(item.role || 'unknown')}</span><span class="trace-preview">${esc(item.preview || `${item.chars || 0} chars`)}</span>${tools}</div>`;
      }).join('')}</div>`;
    }
    function renderRequest(row) {
      const preview = requestPreview(row);
      const slot = row.selectedCodexSlotIndex ?? '-';
      const account = row.selectedCodexAccountKey || '';
      const total = row.totalResponseTimeMs || 0;
      const latencyKind = row.latencySource === 'legacy_response_time' ? 'legacy' : 'first';
      const cachePercent = row.context?.native_cache?.version === 2 && !row.context.native_cache.cache_read_known ? 'unknown' : tokenCachePercent(row.cachedInputTokens, row.inputTokens);
      const reasoning = row.requestedReasoningEffort || row.context?.requested_reasoning_effort || 'default';
      const tools = row.actualToolCallCount == null ? 'unknown'
        : `${row.actualToolCallCount}${row.toolTelemetryComplete === false ? '+ calls · partial' : ' calls'}`;
      const toolNames = Array.isArray(row.actualToolNames) ? row.actualToolNames : [];
      return `<details class="request${row.error ? ' error' : ''}">
        <summary title="${esc(preview)}">
          <span class="request-primary"><strong class="${row.error ? 'bad' : ''}">${esc(row.error ? 'Error' : 'OK')}</strong><span class="muted">${esc(fmtTime(row.timestamp))}</span></span>
          <span class="request-field request-model"><span class="field-label">Model</span>${esc(row.actualModel)}</span>
          <span class="request-field request-cache"><span class="field-label">Cache</span>${esc(cachePercent)}</span>
          <span class="request-field request-reasoning"><span class="field-label">Reasoning</span>${esc(reasoning)}</span>
          <span class="request-field request-tools"><span class="field-label">Tools</span>${esc(tools)}</span>
          <span class="request-field request-latency"><span class="field-label">Latency</span>${esc(row.responseTimeMs || 0)}ms ${esc(latencyKind)} · ${esc(total)}ms total</span>
        </summary>
        <div class="request-detail">
          <div class="request-preview">${esc(preview)}</div>
          <div class="request-meta">
            <span class="meta-pill">${esc(row.originalModel)} to ${esc(row.routedModel)}</span>
            <span class="meta-pill">${esc(row.tier || 'unknown')}</span>
            <span class="meta-pill">${num(row.inputTokens)} in · ${num(row.cachedInputTokens)} cached · ${num(row.outputTokens)} out</span>
            ${row.toolsAvailable ? '<span class="meta-pill">tools offered</span>' : ''}
            ${toolNames.length ? `<span class="meta-pill">called ${esc(toolNames.join(', '))}</span>` : ''}
            ${row.context?.request_trace?.delta?.phase ? `<span class="meta-pill">${esc(row.context.request_trace.delta.phase)}</span>` : ''}
          </div>
          <p class="muted">${esc(nativeCacheDetail(row.context?.native_cache))}</p>
          ${renderTraceItems(row)}
          <details class="diagnostics">
            <summary>Technical identifiers</summary>
            <div class="diagnostic-list">
              <span>request ${esc(row.requestId || 'unavailable')}</span>
              <button type="button" data-copy="${esc(row.requestId || '')}">Copy request ID</button>
              <span>slot ${esc(slot)}${account ? ` · account ${esc(account)}` : ''}</span>
            </div>
          </details>
        </div>
      </details>`;
    }
    async function getJson(path, signal = undefined) {
      const response = await fetch(path, { headers: headers(), signal });
      let payload = null;
      try { payload = await response.json(); } catch (_error) { payload = null; }
      if (!response.ok) {
        throw new Error(payload?.error?.message || `${path} HTTP ${response.status}`);
      }
      return payload;
    }
    async function loadTurn(turnId, target) {
      target.innerHTML = '<p class="muted">Loading turn...</p>';
      const payload = await getJson(`/api/routing/turns/${encodeURIComponent(turnId)}`);
      const rows = payload.requests || [];
      target.innerHTML = `<div class="request-list">${rows.map(renderRequest).join('') || '<div class="muted">No request detail is available.</div>'}</div>`;
    }
    function wireLazyTurns() {
      sessionsEl.querySelectorAll('details.turn').forEach(item => {
        item.addEventListener('toggle', async () => {
          if (!item.open || item.dataset.loaded === '1') return;
          const target = item.querySelector('.turn-requests');
          try {
            await loadTurn(item.dataset.turnId, target);
            item.dataset.loaded = '1';
          } catch (error) {
            target.innerHTML = `<p class="bad">${esc(error.message)}</p>`;
          }
        });
      });
    }
    function renderStats(stats) {
      const totals = stats.totals || {};
      const today = stats.today || {};
      const latency = today.firstResponseLatencyMs || stats.latencyToday || {};
      const totalLatency = today.totalLatencyMs || {};
      const firstLabel = today.splitLatencyRequests ? 'First response' : 'Legacy response';
      const latencyExtra = [
        `p90 ${latency.p90 == null ? '-' : `${latency.p90}ms`}`,
        today.splitLatencyRequests ? `${today.splitLatencyRequests} split` : '',
        today.legacyLatencyRequests ? `${today.legacyLatencyRequests} legacy` : '',
      ].filter(Boolean).join(' · ');
      const todayLikely = Number(today.cacheLikelyRequests || 0);
      const todayLow = Number(today.cacheLowRequests || 0);
      const allLikely = Number(totals.cacheLikelyRequests || 0);
      const allLow = Number(totals.cacheLowRequests || 0);
      const todayCacheDetail = [
        `${num(today.cachedInputTokens)} cached`,
        likelyUsageCacheText(today),
        nativeCacheSummary(today.nativeCache),
        cacheLowSummary(todayLow, todayLikely),
      ].filter(Boolean).join(' · ');
      const allCacheDetail = [
        `${num(totals.requests)} usage rows`,
        likelyUsageCacheText(totals),
        nativeCacheSummary(totals.nativeCache),
        cacheLowSummary(allLow, allLikely),
      ].filter(Boolean).join(' · ');
      metricsEl.innerHTML = [
        metric('Today requests', today.loggedRequests || 0, `${today.usageSeenRequests || 0} with usage`),
        metric('Today usage cache', today.usageSeenRequests ? cacheUsagePercent(today.inputTokens, today.cachedInputTokens, today.nativeCache) : 'unknown', todayCacheDetail),
        metric(firstLabel, latency.avg == null ? '-' : `${latency.avg}ms`, latencyExtra),
        metric('Total duration', totalLatency.avg == null ? '-' : `${totalLatency.avg}ms`, `p90 ${totalLatency.p90 == null ? '-' : `${totalLatency.p90}ms`}`),
        metric('Today errors', today.errors || 0, `${today.usageMissingRequests || 0} missing usage`),
        metric('All usage cache', totals.usageSeenRequests ? cacheUsagePercent(totals.inputTokens, totals.cachedInputTokens, totals.nativeCache) : 'unknown', allCacheDetail),
      ].join('');
    }
    function populateFilter(select, values, allLabel, pending = '') {
      const current = select.value || pending;
      select.innerHTML = `<option value="">${esc(allLabel)}</option>${(values || []).map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('')}`;
      if ([...select.options].some(option => option.value === current)) select.value = current;
      select.dataset.pending = '';
    }
    function renderFilterState(payload) {
      const options = payload.filterOptions || {};
      populateFilter(modelFilter, options.models, 'All models', modelFilter.dataset.pending);
      populateFilter(reasoningFilter, options.reasoningEfforts, 'All reasoning', reasoningFilter.dataset.pending);
      const active = Object.entries(payload.appliedFilters || {}).filter(([_key, value]) => value);
      filterStatusEl.textContent = active.length
        ? `${payload.matchingTurns || 0} matching turns · ${active.map(([key, value]) => `${key}: ${value}`).join(' · ')}`
        : '';
    }
    function renderSessions(payload) {
      const sessions = payload.sessions || [];
      statusEl.textContent = `${sessions.length} shown of ${payload.matchingSessions ?? sessions.length}${payload.hasMoreSessions ? ', more matches' : ''}`;
      sessionsEl.innerHTML = sessions.map((session, index) => {
        const turns = session.turns || [];
        const latestTurn = turns[0] || {};
        const title = activityTitle(latestTurn, `Session at ${fmtTime(session.lastAt)}`);
        return `<details class="panel session-card" ${index === 0 ? 'open' : ''}>
          <summary class="session-summary">
            <span class="title"><strong class="activity-title"><span class="chev">›</span>${esc(title)}</strong><span class="muted">${esc(fmtTime(session.lastAt))} · ${esc(session.turnCount || turns.length)} turns</span></span>
            <span class="summary-metrics">${metricLine(session.metrics, session.models)}</span>
          </summary>
          <div class="turn-list">
            ${turns.map(turn => `<details class="turn" data-turn-id="${esc(turn.id)}">
              <summary class="turn-summary">
                <span class="title"><strong class="activity-title"><span class="chev">›</span>${esc(activityTitle(turn, 'Turn without preview'))}</strong><span class="muted">${esc(fmtTime(turn.lastAt))}</span></span>
                <span class="summary-metrics">${metricLine(turn.metrics, turn.models)}</span>
              </summary>
              <div class="turn-requests"></div>
              <details class="diagnostics">
                <summary>Turn identifier</summary>
                <div class="diagnostic-list"><span>${esc(turn.id)}</span><button type="button" data-copy="${esc(turn.id)}">Copy turn ID</button></div>
              </details>
            </details>`).join('')}
            ${session.hasMoreTurns ? '<p class="muted">Older turns hidden by the current limit.</p>' : ''}
            <details class="diagnostics session-diagnostics">
              <summary>Session identifier</summary>
              <div class="diagnostic-list"><span>${esc(session.id)}</span><button type="button" data-copy="${esc(session.id)}">Copy session ID</button></div>
            </details>
          </div>
        </details>`;
      }).join('') || '<div class="panel muted" role="status">No sessions recorded yet. New routed requests will appear here.</div>';
      wireLazyTurns();
    }
    sessionsEl.addEventListener('click', async event => {
      const button = event.target.closest('button[data-copy]');
      if (!button) return;
      try {
        await navigator.clipboard.writeText(button.dataset.copy || '');
        const label = button.textContent;
        button.textContent = 'Copied';
        setTimeout(() => { button.textContent = label; }, 1200);
      } catch (_error) {
        button.textContent = 'Copy failed';
      }
    });
    async function load() {
      const generation = ++loadGeneration;
      if (activeLoadController) activeLoadController.abort();
      const controller = new AbortController();
      activeLoadController = controller;
      try {
        statusEl.textContent = 'Loading...';
        const filters = selectedFilters();
        const query = new URLSearchParams({ sessionLimit: '30', turnLimit: '12' });
        for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
        const [stats, live] = await Promise.all([
          getJson('/stats', controller.signal),
          getJson(`/api/routing/live?${query}`, controller.signal),
        ]);
        if (generation !== loadGeneration || controller.signal.aborted) return;
        renderStats(stats);
        renderFilterState(live);
        renderSessions(live);
      } catch (error) {
        if (generation !== loadGeneration || controller.signal.aborted) return;
        statusEl.textContent = '';
        sessionsEl.innerHTML = `<div class="panel bad">${esc(error.message)}</div>`;
      }
    }
    load();
