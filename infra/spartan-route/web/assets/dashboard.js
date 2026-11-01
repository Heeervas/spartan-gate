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
      return `${native.requests} observed requests · ${compatible} compatible · ${Number(comparisons.changed || 0)} changed · ${unknown} unknown · cache data ${Number(native.cacheReadKnownRequests || 0)}/${Number(native.usageRequests || 0)}`;
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
    function fact(label, value, tone = '', title = '') {
      return `<span class="fact ${tone}"${title ? ` title="${esc(title)}"` : ''}><span class="fact-label">${esc(label)}</span><strong>${esc(value)}</strong></span>`;
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
    function speedBreakdown(counts = {}) {
      const tiers = [
        ['priority', 'Fast', 1], ['ultrafast', 'Ultrafast', 2], ['default', 'Normal', 0],
        ['auto', 'Auto', 0], ['flex', 'Flex', 0], ['scale', 'Scale', 0],
        ['unspecified', 'Unspecified', 0], ['unknown', 'Unknown', 0],
      ];
      const parts = tiers.filter(([tier]) => Number(counts[tier]) > 0).map(([tier, label, bolts]) => {
        const path = '<path d="M9 1 2 9h5l-1 6 8-9H9z"/>';
        const icon = bolts ? `<svg class="speed-icon" viewBox="0 0 ${bolts * 16} 16" aria-hidden="true" focusable="false">${path}${bolts === 2 ? '<path d="m25 1-7 8h5l-1 6 8-9h-5z"/>' : ''}</svg>` : '';
        return `<span class="speed-tier">${icon}${label} · ${num(counts[tier])}</span>`;
      });
      return `${parts.length > 1 ? 'mixed · ' : ''}${parts.join(', ') || 'Unknown'}`;
    }
    function speedFact(speed = {}) {
      return `<span class="fact speed" title="Requested is client intent, not confirmation. Served is provider-reported, not measured latency. Counts cover only requests in this view."><span class="fact-label">Speed</span><strong><span class="speed-line">Requested: ${speedBreakdown(speed.requested)}</span><span class="speed-line">Served: ${speedBreakdown(speed.served)}</span></strong></span>`;
    }
    function metricLine(metrics = {}, models = []) {
      const cache = Number(metrics.usageSeenRequests || 0) ? cacheUsagePercent(metrics.inputTokens, metrics.cachedInputTokens, metrics.nativeCache) : 'unknown';
      const errors = Number(metrics.errors || 0);
      return [
        fact('Model', models.join(', ') || 'unknown'),
        fact('Cache', cache),
        fact('Cache observations', metrics.nativeCache?.requests ? `${num(metrics.nativeCache.requests)} requests` : 'none yet', '', nativeCacheSummary(metrics.nativeCache)),
        fact('Est. usage', usageUnitSummary(metrics)),
        speedFact(metrics.speed),
        fact('Reasoning', reasoningSummary(metrics)),
        fact('Tools', toolSummary(metrics)),
        fact('Status', errors ? `${errors} errors` : 'OK', errors ? 'bad' : ''),
      ].join('');
    }
    function activityTitle(item, fallback) {
      const preview = String(item?.promptPreview || '').trim();
      return preview || fallback;
    }
    let detailScriptPromise = null;
    const turnLoads = new Map();
    function detailRenderer() {
      if (window.SpartanRouteDetails) return Promise.resolve(window.SpartanRouteDetails);
      if (detailScriptPromise) return detailScriptPromise;
      detailScriptPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        const timer = setTimeout(() => fail(), 5000);
        function fail() {
          clearTimeout(timer);
          script.remove();
          reject(new Error('Turn details could not load. Close and reopen the turn to retry.'));
        }
        script.src = '/dashboard-assets/dashboard-details.js';
        script.onload = () => {
          clearTimeout(timer);
          if (window.SpartanRouteDetails) resolve(window.SpartanRouteDetails);
          else fail();
        };
        script.onerror = fail;
        document.head.appendChild(script);
      }).catch(error => { detailScriptPromise = null; throw error; });
      return detailScriptPromise;
    }
    async function getJson(path, signal = undefined, retry = true) {
      const controller = new AbortController();
      const abort = () => controller.abort();
      if (signal?.aborted) abort();
      else signal?.addEventListener('abort', abort, { once: true });
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 5000);
      try {
        const response = await fetch(path, { headers: headers(), signal: controller.signal });
        const payload = await response.json();
        if (!response.ok) {
          const error = new Error(payload?.error?.message || `${path} HTTP ${response.status}`);
          error.transient = response.status >= 500;
          throw error;
        }
        return payload;
      } catch (error) {
        // Aborting the browser request does not stop an already-running server
        // query. Let the next scheduled poll retry instead of doubling its work.
        if (retry && !timedOut && !signal?.aborted && (error.transient || error instanceof TypeError)) {
          return getJson(path, signal, false);
        }
        if (timedOut) throw new Error('Request timed out after 5 seconds. Refresh to retry.');
        throw error;
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
      }
    }
    function cancelTurnLoads() {
      for (const entry of turnLoads.values()) entry.controller.abort();
      turnLoads.clear();
    }
    function turnLoadKey(sessionId, turnId) {
      return `${sessionId || ''}\0${turnId}`;
    }
    function loadTurn(turnId, target, item) {
      const sessionId = item.dataset.sessionId;
      const key = turnLoadKey(sessionId, turnId);
      const existing = turnLoads.get(key);
      if (existing && existing.target === target) return existing.promise;
      existing?.controller.abort();
      const controller = new AbortController();
      const credential = tokenInput.value.trim();
      const version = item.dataset.detailVersion;
      const entry = { controller, target, promise: null };
      target.setAttribute('aria-busy', 'true');
      if (!target.children.length) target.innerHTML = '<p class="muted">Loading turn...</p>';
      const current = () => !controller.signal.aborted && credential === tokenInput.value.trim()
        && target.isConnected && item.dataset.detailVersion === version && turnLoads.get(key) === entry;
      entry.promise = Promise.all([
        detailRenderer(),
        getJson(`/api/routing/turns/${encodeURIComponent(turnId)}${sessionId ? `?sessionId=${encodeURIComponent(sessionId)}` : ''}`, controller.signal),
      ]).then(([renderer, payload]) => {
        if (!current()) return;
        const rows = payload.requests || [];
        renderer.renderRequests(target, rows);
        item.dataset.loaded = '1';
      }).catch(error => {
        if (current()) target.innerHTML = `<p class="bad" role="alert">${esc(error.message)}</p>`;
      }).finally(() => {
        if (turnLoads.get(key) === entry) {
          turnLoads.delete(key);
          target.setAttribute('aria-busy', 'false');
        }
      });
      turnLoads.set(key, entry);
      return entry.promise;
    }
    function wireLazyTurns() {
      sessionsEl.querySelectorAll('details.turn').forEach(item => {
        if (!item.dataset.wired) {
          item.dataset.wired = '1';
          item.addEventListener('toggle', () => {
            if (item.open && item.dataset.loaded !== '1') loadTurn(item.dataset.turnId, item.querySelector('.turn-requests'), item);
          });
        }
        if (item.open && item.dataset.loaded !== '1') loadTurn(item.dataset.turnId, item.querySelector('.turn-requests'), item);
      });
    }
    const renderedMarkup = new WeakMap();
    function stableHTML(element, markup) {
      if (renderedMarkup.get(element) === markup) return;
      element.innerHTML = markup;
      renderedMarkup.set(element, markup);
    }
    function restoreFocus(element) {
      if (element?.isConnected && document.activeElement !== element) element.focus({ preventScroll: true });
    }
    function retainSessionDiagnostics(next, old) {
      const diagnostic = old.querySelector('.session-diagnostics');
      if (!diagnostic) return;
      const incoming = next.querySelector('.session-diagnostics');
      if (diagnostic.innerHTML !== incoming.innerHTML) diagnostic.innerHTML = incoming.innerHTML;
      incoming.replaceWith(diagnostic);
    }
    function retainDetails(incoming, previous, selector, key) {
      const existing = new Map([...previous.querySelectorAll(selector)].map(item => [item.dataset[key], item]));
      incoming.querySelectorAll(selector).forEach(next => {
        const old = existing.get(next.dataset[key]);
        if (!old) return;
        const summary = old.querySelector('summary');
        const nextSummary = next.querySelector('summary');
        if (summary.innerHTML !== nextSummary.innerHTML) summary.innerHTML = nextSummary.innerHTML;
        if (key === 'sessionId') {
          retainDetails(next, old, 'details.turn', 'turnId');
          // Keep the session node and its disclosure state while reusing its turns.
          retainSessionDiagnostics(next, old);
          old.querySelector('.turn-list').replaceChildren(...next.querySelector('.turn-list').childNodes);
        } else if (old.dataset.detailVersion !== next.dataset.detailVersion) {
          old.dataset.detailVersion = next.dataset.detailVersion;
          delete old.dataset.loaded;
          const key = turnLoadKey(old.dataset.sessionId, old.dataset.turnId);
          const entry = turnLoads.get(key);
          if (entry) { entry.controller.abort(); turnLoads.delete(key); }
        }
        next.replaceWith(old);
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
      stableHTML(metricsEl, [
        metric('Today requests', today.loggedRequests || 0, `${today.usageSeenRequests || 0} with usage`),
        metric('Today usage cache', today.usageSeenRequests ? cacheUsagePercent(today.inputTokens, today.cachedInputTokens, today.nativeCache) : 'unknown', todayCacheDetail),
        metric(firstLabel, latency.avg == null ? '-' : `${latency.avg}ms`, latencyExtra),
        metric('Total duration', totalLatency.avg == null ? '-' : `${totalLatency.avg}ms`, `p90 ${totalLatency.p90 == null ? '-' : `${totalLatency.p90}ms`}`),
        metric('Today errors', today.errors || 0, `${today.usageMissingRequests || 0} missing usage`),
        metric('All usage cache', totals.usageSeenRequests ? cacheUsagePercent(totals.inputTokens, totals.cachedInputTokens, totals.nativeCache) : 'unknown', allCacheDetail),
      ].join(''));
    }
    function populateFilter(select, values, allLabel, pending = '') {
      const current = select.value || pending;
      stableHTML(select, `<option value="">${esc(allLabel)}</option>${(values || []).map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('')}`);
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
    function clientCorrelation(session) {
      const correlation = session.correlation;
      return correlation?.version === 1 && correlation.source === 'client_asserted' ? correlation : null;
    }
    function clientLabel(client) {
      return client === 'ultimate' ? 'Ultimate' : 'Pi';
    }
    function purposeLabel(purpose) {
      return ({ agent: 'Agent', summary: 'Summary', compaction: 'Compaction', other: 'Other' })[purpose] || 'Other';
    }
    function correlationSummary(session) {
      const correlation = clientCorrelation(session);
      if (!correlation) return '<span class="meta-pill">Legacy identity</span>';
      const purposes = (session.purposes || [correlation.purpose]).map(purposeLabel).join(' + ');
      return `<span class="meta-pill">${esc(clientLabel(correlation.client))} · ${esc(purposes)}</span>`;
    }
    function correlationDetails(session, sessions) {
      const correlation = clientCorrelation(session);
      if (!correlation) return '<p class="muted">Existing signed-chain or cache-key identity. Historical requests are not reclassified.</p>';
      const relations = ['parent', 'root'].filter(key => correlation[key] && correlation[key] !== session.id).map(key => {
        const target = sessions.find(item => item.id === correlation[key] && clientCorrelation(item)?.client === correlation.client);
        const label = key === 'parent' ? 'Parent' : 'Root';
        return `<div class="diagnostic-list"><span>${label}: ${esc(correlation[key])}</span>${target
          ? `<button type="button" data-session-jump="${esc(target.id)}">View ${label.toLowerCase()}</button>`
          : '<span class="muted">not in this view</span>'}</div>`;
      }).join('');
      return `<p class="muted">Client asserted · observational only, not an authentication or cache boundary.</p>${relations}`;
    }
    function groupSessions(sessions) {
      const groups = new Map();
      for (const session of sessions) {
        const correlation = clientCorrelation(session);
        const key = correlation?.root ? `${correlation.client}:${correlation.root}` : `session:${session.id}`;
        if (!groups.has(key)) groups.set(key, { root: correlation?.root, client: correlation?.client, sessions: [] });
        groups.get(key).sessions.push(session);
      }
      return [...groups.values()];
    }
    function sessionCard(session, index, sessions) {
      const turns = session.turns || [];
      const latestTurn = turns[0] || {};
      const title = activityTitle(latestTurn, `Session at ${fmtTime(session.lastAt)}`);
      return `<details class="panel session-card" data-session-id="${esc(session.id)}" ${index === 0 ? 'open' : ''}>
          <summary class="session-summary">
            <span class="title"><strong class="activity-title"><span class="chev">›</span>${esc(title)}</strong><span class="muted">${esc(fmtTime(session.lastAt))} · ${esc(session.turnCount || turns.length)} turns</span>${correlationSummary(session)}</span>
            <span class="summary-metrics">${metricLine(session.metrics, session.models)}</span>
          </summary>
          <div class="turn-list">
            ${turns.map(turn => `<details class="turn" data-turn-id="${esc(turn.id)}" data-session-id="${esc(session.id)}" data-detail-version="${esc(JSON.stringify([turn.lastAt, turn.metrics]))}">
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
              <summary>Session identity and relationships</summary>
              ${correlationDetails(session, sessions)}
              <div class="diagnostic-list"><span>${esc(session.id)}</span><button type="button" data-copy="${esc(session.id)}">Copy session ID</button></div>
            </details>
          </div>
        </details>`;
    }
    function renderSessions(payload) {
      const sessions = payload.sessions || [];
      statusEl.textContent = `${sessions.length} shown of ${payload.matchingSessions ?? sessions.length}${payload.hasMoreSessions ? ', more matches' : ''}`;
      const markup = groupSessions(sessions).map(group => {
        const cards = group.sessions.map(session => sessionCard(session, sessions.indexOf(session), sessions)).join('');
        if (group.sessions.length < 2) return cards;
        return `<section class="related-group" aria-label="Related ${esc(clientLabel(group.client))} conversations">
          <div class="related-heading"><strong>${esc(clientLabel(group.client))} · ${group.sessions.length} related conversations</strong><span class="muted">Client asserted · children stay separate</span></div>${cards}</section>`;
      }).join('') || '<div class="panel muted" role="status">No sessions recorded yet. New routed requests will appear here.</div>';
      if (renderedMarkup.get(sessionsEl) === markup) return;
      const focusedElement = document.activeElement;
      const template = document.createElement('template');
      template.innerHTML = markup;
      retainDetails(template.content, sessionsEl, 'details.session-card', 'sessionId');
      sessionsEl.replaceChildren(...template.content.childNodes);
      restoreFocus(focusedElement);
      renderedMarkup.set(sessionsEl, markup);
      for (const [id, entry] of turnLoads) {
        if (!entry.target.isConnected) { entry.controller.abort(); turnLoads.delete(id); }
      }
      wireLazyTurns();
    }
    sessionsEl.addEventListener('click', async event => {
      const jump = event.target.closest('button[data-session-jump]');
      if (jump) {
        const target = [...sessionsEl.querySelectorAll('details.session-card')].find(item => item.dataset.sessionId === jump.dataset.sessionJump);
        if (target) { target.open = true; target.querySelector('summary').focus(); target.scrollIntoView({ block: 'nearest' }); }
        return;
      }
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
    let pollTimer = null;
    let renderedCredential = tokenInput.value.trim();
    function schedulePoll() {
      clearTimeout(pollTimer);
      if (!document.hidden) pollTimer = setTimeout(() => load(true), 15000);
    }
    document.addEventListener('visibilitychange', () => {
      clearTimeout(pollTimer);
      if (!document.hidden) load(true);
    });
    tokenInput.addEventListener('input', () => {
      ++loadGeneration;
      activeLoadController?.abort();
      cancelTurnLoads();
      sessionsEl.replaceChildren();
      metricsEl.replaceChildren();
      renderedMarkup.delete(sessionsEl);
      renderedMarkup.delete(metricsEl);
      clearTimeout(pollTimer);
      statusEl.textContent = 'Use token to load this account.';
    });
    async function load(background = false) {
      clearTimeout(pollTimer);
      if (background && activeLoadController && !activeLoadController.signal.aborted) return;
      if (renderedCredential !== tokenInput.value.trim()) {
        renderedCredential = tokenInput.value.trim();
        cancelTurnLoads();
        sessionsEl.replaceChildren();
        metricsEl.replaceChildren();
        renderedMarkup.delete(sessionsEl);
        renderedMarkup.delete(metricsEl);
      }
      const generation = ++loadGeneration;
      if (activeLoadController) activeLoadController.abort();
      const controller = new AbortController();
      activeLoadController = controller;
      try {
        if (!background) statusEl.textContent = 'Loading...';
        const filters = selectedFilters();
        const query = new URLSearchParams({ sessionLimit: '30', turnLimit: '12' });
        for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
        const current = () => generation === loadGeneration && !controller.signal.aborted;
        const results = await Promise.allSettled([
          getJson('/stats', controller.signal).then(stats => {
            if (current()) renderStats(stats);
          }),
          getJson(`/api/routing/live?${query}`, controller.signal).then(live => {
            if (!current()) return;
            renderFilterState(live);
            renderSessions(live);
          }),
        ]);
        if (!current()) return;
        const failures = results.flatMap((result, index) => result.status === 'rejected'
          ? [`${index === 0 ? 'Summary' : 'Sessions'}: ${result.reason.message}`] : []);
        if (failures.length) {
          const retained = results[1].status === 'rejected' && sessionsEl.children.length;
          statusEl.textContent = `${failures.join(' · ')}${retained ? ' · showing last successful sessions' : ''}`;
        }
      } catch (error) {
        if (generation !== loadGeneration || controller.signal.aborted) return;
        statusEl.textContent = `${error.message}${sessionsEl.children.length ? ' · showing last successful data' : ''}`;
      } finally {
        if (generation === loadGeneration) { activeLoadController = null; schedulePoll(); }
      }
    }
    load();
