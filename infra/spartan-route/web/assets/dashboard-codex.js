    const tokenInput = document.getElementById('token');
    const accountsEl = document.getElementById('accounts');
    const usageStatus = document.getElementById('usage-status');
    const modelUsageEl = document.getElementById('model-usage');
    const modelUsageMeta = document.getElementById('model-usage-meta');
    const efficiencyEl = document.getElementById('efficiency');
    const efficiencyMeta = document.getElementById('efficiency-meta');
    const cacheEl = document.getElementById('cache');
    const leasesEl = document.getElementById('leases');
    const leaseSummary = document.getElementById('lease-summary');
    const errorsEl = document.getElementById('errors');
    tokenInput.value = sessionStorage.getItem('spartanRouteToken') || '';

    document.getElementById('save').onclick = () => {
      sessionStorage.setItem('spartanRouteToken', tokenInput.value.trim());
      load();
    };
    document.getElementById('refresh').onclick = () => load();
    document.getElementById('force').onclick = () => load(true);

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
    function fmtUnits(value) {
      const units = Number(value || 0);
      return units >= 100 ? units.toFixed(0) : units.toFixed(1);
    }
    function fmtTime(value) {
      if (!value) return '-';
      const d = new Date(deadlineMs(value));
      return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
    }
    function deadlineMs(value) {
      if (!value) return NaN;
      if (typeof value === 'number' || /^\d+(\.\d+)?$/.test(String(value))) {
        const numeric = Number(value);
        return numeric < 1000000000000 ? numeric * 1000 : numeric;
      }
      return Date.parse(value);
    }
    function countdownText(value) {
      const target = deadlineMs(value);
      if (!Number.isFinite(target)) return 'unknown';
      const remaining = Math.max(0, Math.floor((target - Date.now()) / 1000));
      const days = Math.floor(remaining / 86400);
      const hours = Math.floor((remaining % 86400) / 3600);
      const minutes = Math.floor((remaining % 3600) / 60);
      const seconds = remaining % 60;
      if (days > 0) return `${days}d ${hours}h ${minutes}m ${seconds}s`;
      if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
      return `${minutes}m ${seconds}s`;
    }
    function resetTitle(value) {
      return value ? `Resets ${fmtTime(value)}` : 'No reset timestamp';
    }
    function cooldown(until) {
      if (!until) return '-';
      const target = deadlineMs(until);
      return Number.isFinite(target) && target > Date.now() ? countdownText(until) : '-';
    }
    function updateCountdowns() {
      document.querySelectorAll('[data-countdown]').forEach(element => {
        const prefix = element.getAttribute('data-countdown-prefix') || '';
        element.textContent = `${prefix}${countdownText(element.getAttribute('data-countdown'))}`;
      });
    }
    function pct(cached, input) {
      return input > 0 ? `${((cached / input) * 100).toFixed(1)}%` : '0.0%';
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
    function likelyUsageCacheText(row = {}) {
      const likely = Number(row.cache_likely_requests || row.cacheLikelyRequests || 0);
      const input = Number(row.cache_likely_input_tokens || row.cacheLikelyInputTokens || 0);
      if (!likely && !input) return '';
      if (row.cache_likely_hit_percent !== undefined) return `${Number(row.cache_likely_hit_percent || 0).toFixed(1)}% legacy likely cache`;
      if (row.cacheLikelyHitPercent !== undefined) return `${Number(row.cacheLikelyHitPercent || 0).toFixed(1)}% legacy likely cache`;
      const cached = Number(row.cache_likely_cached_input_tokens || row.cacheLikelyCachedInputTokens || 0);
      return `${pct(cached, input)} legacy likely cache`;
    }
    function balanceTone(value) {
      if (value >= 90) return 'var(--bad)';
      if (value >= 70) return 'var(--warn)';
      return 'var(--good)';
    }
    function balanceRing(windowName, row, secondary = false) {
      if (!row) return '';
      const used = Math.max(0, Math.min(100, Number(
        row.usedPercent ?? (100 - Number(row.remainingPercent || 0))
      )));
      const multiplier = Number(row.capacityMultiplier || 1);
      const usedUnits = Number(row.usedUnits ?? (used * multiplier));
      const capacityUnits = Number(row.capacityUnits ?? (100 * multiplier));
      const resetCopy = row.resetAt ? `resets in ${countdownText(row.resetAt)}` : 'reset time unavailable';
      return `<div class="ring-wrap${secondary ? ' secondary' : ''}">
        <div class="ring" style="--degrees:${used * 3.6}deg;--tone:${balanceTone(used)}"><span class="ring-value">${esc(fmtUnits(usedUnits))}u</span></div>
        <div>
          <strong>${esc(windowName)}</strong>
          <div class="usage-primary"><strong>${esc(fmtUnits(usedUnits))} / ${esc(fmtUnits(capacityUnits))} units</strong></div>
          <div class="muted">${used.toFixed(1)}% raw · x${esc(multiplier)}</div>
          <div class="muted" title="${esc(resetTitle(row.resetAt))}"${row.resetAt ? ` data-countdown-prefix="resets in " data-countdown="${esc(row.resetAt)}"` : ''}>${esc(resetCopy)}</div>
        </div>
      </div>`;
    }
    function slotPathText(paths) {
      const values = Array.isArray(paths) ? paths.filter(Boolean) : [];
      return values.length ? values.join(', ') : '-';
    }
    function activeLeaseStrip(account, leases) {
      const slotIndexes = new Set((account.slotIndexes || []).map(Number));
      const lease = (leases || []).find(item => item.accountKey === account.accountKey || slotIndexes.has(Number(item.slotIndex)));
      if (!lease) return '';
      return `<div class="active-lease-strip">
        <strong>Active cache lease</strong>
        <div class="muted">slot ${esc(lease.slotIndex)} · last ${esc(fmtTime(lease.lastUsedAt))} · expires <span title="${esc(fmtTime(lease.nominalExpiresAt))}" data-countdown-prefix="" data-countdown="${esc(lease.nominalExpiresAt)}">${esc(countdownText(lease.nominalExpiresAt))}</span></div>
      </div>`;
    }
    function resetCreditStrip(credits) {
      const creditCount = credits ? credits.availableCount : '-';
      const creditItems = credits && Array.isArray(credits.credits) ? credits.credits : [];
      return `<div class="reset-credit-strip">
        <strong>Reset credits ${esc(creditCount)}</strong>
        <div>${creditItems.slice(0, 4).map(item => `<span class="pill">${esc(item.status || 'available')} · expires ${esc(fmtTime(item.expiresAt))}</span>`).join('') || '<span class="pill">no reset-credit detail</span>'}</div>
      </div>`;
    }
    function slotStateLine(slot) {
      const cooldownText = cooldown(slot.rateLimitedUntil) !== '-' ? `rate ${cooldown(slot.rateLimitedUntil)}` : cooldown(slot.transientUntil);
      const routing = slot.routingEnabled ? 'enabled' : 'disabled';
      const auth = slot.authAvailable ? 'ready' : (slot.authUnavailableReason || 'unavailable');
      const mismatch = slot.routingIdentityMismatch
        ? (slot.requestedRoutingEnabled ? ' · identity mismatch' : ' · stale disable ignored')
        : (slot.routingIdentityUnverified ? ' · identity unverified' : '');
      return `${routing} · ${auth}${cooldownText !== '-' ? ` · cooldown ${cooldownText}` : ''}${mismatch}`;
    }
    function authLifecycle(slot) {
      const source = slot.authStateSource || (slot.authAvailable ? 'bootstrap' : 'unknown');
      const expiry = slot.authExpiresAt
        ? `<span title="${esc(fmtTime(slot.authExpiresAt))}" data-countdown-prefix="expires in " data-countdown="${esc(slot.authExpiresAt)}">expires in ${esc(countdownText(slot.authExpiresAt))}</span>`
        : '<span>expiry unknown</span>';
      const refreshed = slot.authLastRefreshAt ? ` · refreshed ${esc(fmtTime(slot.authLastRefreshAt))}` : '';
      const persistence = slot.authPersistenceHealthy === false
        ? ` · persistence unavailable (${esc(slot.authErrorCode || 'auth_store_unavailable')})`
        : '';
      return `<div class="auth-lifecycle ${slot.authPersistenceHealthy === false ? 'warn' : 'muted'}"><strong>OAuth ${esc(source)}</strong>${expiry}<span>${refreshed}${persistence}</span></div>`;
    }
    function renderSlotControls(slot) {
      const tone = slot.healthStatus === 'failed' ? 'bad' : (slot.healthStatus === 'unavailable' ? 'warn' : (slot.healthStatus === 'healthy' ? 'good' : 'muted'));
      return `<div class="slot-row">
        <div class="slot-row-main">
          <strong>slot ${esc(slot.slotIndex)}</strong>
          <div class="muted">${esc(slot.accountKey || '-')} · ${esc(slot.slotPath || slot.slotPathHash || 'no path')}</div>
          <div class="${tone}">${esc(slotStateLine(slot))}</div>
          ${authLifecycle(slot)}
        </div>
        <div class="slot-actions">
          <button data-action="toggle" data-slot="${esc(slot.slotIndex)}" data-next="${slot.routingEnabled ? 'false' : 'true'}" class="${slot.routingEnabled ? 'danger' : ''}">${esc(slot.routingEnabled ? 'Disable' : 'Enable')}</button>
          <button data-action="usage" data-slot="${esc(slot.slotIndex)}">Check usage</button>
        </div>
      </div>`;
    }
    function appendUnique(target, values) {
      for (const value of values || []) {
        if (value !== undefined && value !== null && !target.includes(value)) target.push(value);
      }
    }
    function mergeAccountCards(accounts, slots) {
      const byKey = new Map();
      for (const account of accounts || []) {
        const key = account.accountKey || `account:${byKey.size}`;
        const current = byKey.get(key);
        if (current) {
          appendUnique(current.slotIndexes, account.slotIndexes);
          appendUnique(current.slotPaths, account.slotPaths);
          current.fiveHour = current.fiveHour || account.fiveHour;
          current.weekly = current.weekly || account.weekly;
          current.resetCredits = current.resetCredits || account.resetCredits;
          current.capacityMultiplier = current.capacityMultiplier || account.capacityMultiplier;
          current.stale = current.stale && account.stale;
          continue;
        }
        byKey.set(key, {
          ...account,
          slotIndexes: Array.isArray(account.slotIndexes) ? [...account.slotIndexes] : [],
          slotPaths: Array.isArray(account.slotPaths) ? [...account.slotPaths] : [],
          slots: [],
          slotOnly: false,
        });
      }
      for (const slot of slots || []) {
        const key = slot.accountKey || `slot:${slot.slotIndex}`;
        let card = byKey.get(key);
        if (!card) {
          card = {
            accountKey: slot.accountKey || 'unknown',
            slotIndexes: [],
            slotPaths: [],
            slots: [],
            fiveHour: null,
            weekly: null,
            resetCredits: null,
            source: 'slot-only',
            stale: true,
            slotOnly: true,
            capacityMultiplier: slot.capacityMultiplier || 1,
          };
          byKey.set(key, card);
        }
        card.slots.push(slot);
        if (!card.capacityMultiplier && slot.capacityMultiplier) card.capacityMultiplier = slot.capacityMultiplier;
        if (!card.slotIndexes.includes(slot.slotIndex)) card.slotIndexes.push(slot.slotIndex);
        if (slot.slotPath && !card.slotPaths.includes(slot.slotPath)) card.slotPaths.push(slot.slotPath);
      }
      return Array.from(byKey.values()).sort((left, right) => {
        const leftSlot = Math.min(...(left.slotIndexes.length ? left.slotIndexes : [9999]));
        const rightSlot = Math.min(...(right.slotIndexes.length ? right.slotIndexes : [9999]));
        return leftSlot - rightSlot;
      });
    }
    function sourceBadge(account) {
      if (account.slotOnly) return '<span class="pill warn">slot-only</span>';
      return `<span class="pill">${esc(account.source || 'usage')}</span>${account.stale ? '<span class="pill warn">stale</span>' : ''}`;
    }
    function planControl(account) {
      const accountKey = String(account.accountKey || '');
      const valid = /^[0-9a-f]{16}$/.test(accountKey);
      const multiplier = Number(account.capacityMultiplier || 1);
      return `<label class="plan-control">Plan
        <select data-action="capacity" data-account="${esc(accountKey)}"${valid ? '' : ' disabled'}>
          <option value="1"${multiplier === 1 ? ' selected' : ''}>x1</option>
          <option value="5"${multiplier === 5 ? ' selected' : ''}>x5</option>
        </select>
      </label>`;
    }
    function accountHealth(account) {
      const slots = account.slots || [];
      const enabled = slots.filter(slot => slot.healthStatus !== 'disabled');
      if (!slots.length) {
        return { status: 'history', label: 'No slot state', reasons: ['No configured slot metadata'], slotIndexes: [] };
      }
      if (!enabled.length) {
        return { status: 'disabled', label: 'Disabled', reasons: ['All linked slots are disabled'], slotIndexes: slots.map(slot => slot.slotIndex) };
      }
      const priority = { healthy: 0, history: 1, unavailable: 2, failed: 3 };
      const status = enabled.reduce((current, slot) => {
        const candidate = slot.healthStatus || 'healthy';
        return (priority[candidate] || 0) > (priority[current] || 0) ? candidate : current;
      }, 'healthy');
      const affected = enabled.filter(slot => (slot.healthStatus || 'healthy') === status);
      const reasons = [...new Set(affected.flatMap(slot => slot.healthReasons || []))];
      const labels = { failed: 'Failed', unavailable: 'Unavailable', history: 'History', disabled: 'Disabled', healthy: 'Healthy' };
      return {
        status,
        label: labels[status] || 'Unknown',
        reasons: reasons.length ? reasons : ['Ready'],
        slotIndexes: affected.map(slot => slot.slotIndex),
      };
    }
    function renderAccountCards(accounts, slots = [], leases = []) {
      const cards = mergeAccountCards(accounts, slots);
      const healthRows = [];
      accountsEl.innerHTML = cards.map(account => {
        const credits = account.resetCredits;
        const slotsHtml = (account.slots || []).map(renderSlotControls).join('');
        const health = accountHealth(account);
        healthRows.push(health);
        return `<article class="account health-${health.status}${account.slotOnly ? ' slot-only' : ''}">
          <div class="account-head">
            <div class="account-meta">
              <h3>${esc(account.accountKey || 'unknown')}</h3>
              <div class="muted">slots ${(account.slotIndexes || []).map(esc).join(', ') || '-'}</div>
              <div class="muted">${esc(slotPathText(account.slotPaths))}</div>
            </div>
            <div class="badge-row">${planControl(account)}${sourceBadge(account)}<span class="pill health-badge"></span></div>
          </div>
          <div class="account-health" role="status"></div>
          <div class="rings">
            ${balanceRing('Weekly balance', account.weekly)}
            ${balanceRing('Five-hour balance', account.fiveHour, true)}
          </div>
          ${activeLeaseStrip(account, leases)}
          ${resetCreditStrip(credits)}
          ${slotsHtml || '<div class="slot-row muted">No configured slot metadata.</div>'}
        </article>`;
      }).join('') || '<div class="panel muted">No usage telemetry yet.</div>';
      accountsEl.querySelectorAll('article.account').forEach((element, index) => {
        const health = healthRows[index];
        const slotsText = health.slotIndexes.length ? ` · slots ${health.slotIndexes.join(', ')}` : '';
        const summary = `${health.label}${slotsText}: ${health.reasons.join('; ')}`;
        element.querySelector('.health-badge').textContent = health.label;
        element.querySelector('.account-health').textContent = summary;
      });
      updateCountdowns();
    }
    function renderModelUsage(usage = {}) {
      const models = Array.isArray(usage.models) ? usage.models : [];
      const reasonLabels = {
        no_current_weekly_snapshots: 'Waiting for a current weekly balance.',
        no_attributed_usage_tokens: 'No attributable usage in the current window.',
        no_positive_weekly_usage: 'Weekly usage has not advanced yet.',
      };
      if (!usage.available) {
        modelUsageMeta.textContent = usage.observedAt ? `as of ${fmtTime(usage.observedAt)}` : '';
        modelUsageEl.innerHTML = `<tr><td colspan="6" class="muted">${esc(reasonLabels[usage.unavailableReason] || 'Estimate unavailable.')}</td></tr>`;
        return;
      }
      const coverage = [
        `${num(usage.sampleTokens)} calibration tokens`,
        `${num(usage.sampleAccounts)} accounts`,
        usage.excludedAccountRequests ? `${num(usage.excludedAccountRequests)} missing-usage rows excluded` : '',
        usage.unattributedRequests ? `${num(usage.unattributedRequests)} unattributed rows excluded` : '',
      ].filter(Boolean);
      modelUsageMeta.textContent = `${coverage.join(' · ')}${usage.observedAt ? ` · as of ${fmtTime(usage.observedAt)}` : ''}`;
      modelUsageEl.innerHTML = models.map(row => `<tr>
        <td>${esc(row.model)}</td>
        <td>${esc(num(row.requests))}</td>
        <td title="input ${esc(num(row.inputTokens))} · output ${esc(num(row.outputTokens))}">${esc(num(row.totalTokens))}</td>
        <td><strong>${esc(fmtUnits(row.expectedUnits))} units</strong></td>
        <td>${esc(fmtUnits(row.x1EquivalentPercent))}%</td>
        <td>${esc(fmtUnits(row.x5EquivalentPercent))}%</td>
      </tr>`).join('') || '<tr><td colspan="6" class="muted">No model usage in the current weekly window.</td></tr>';
    }
    function renderEfficiency(usage = {}) {
      const rows = Array.isArray(usage.efficiency) ? usage.efficiency : [];
      efficiencyMeta.textContent = usage.available
        ? `${rows.length} groups · current weekly window · account-weighted calibration`
        : 'Estimate unavailable';
      efficiencyEl.innerHTML = rows.map(row => {
        const average = row.averageMillionTokensPerSession == null
          ? 'unknown'
          : Number(row.averageMillionTokensPerSession).toFixed(3);
        const percent = row.percentPerMillionTokens == null
          ? 'unknown'
          : `${fmtUnits(row.percentPerMillionTokens)}%`;
        const missing = Number(row.requestsWithoutSession || 0);
        return `<tr>
          <td>${esc(row.model)}</td>
          <td>${esc(row.reasoning || 'default')}</td>
          <td title="${missing ? `${num(missing)} requests without session IDs excluded from the average` : 'All rows have session IDs'}">${esc(num(row.sessions))}</td>
          <td>${(Number(row.totalTokens || 0) / 1000000).toFixed(2)}</td>
          <td><strong>${esc(average)}</strong></td>
          <td><strong>${esc(percent)}</strong></td>
        </tr>`;
      }).join('') || '<tr><td colspan="6" class="muted">No model and reasoning usage in the current weekly window.</td></tr>';
    }
    function latencyCell(row) {
      const avg = Number(row.avg_response_time_ms || 0);
      const total = Number(row.avg_total_response_time_ms || 0);
      const splitCount = Number(row.split_latency_requests || 0);
      const legacyCount = Number(row.legacy_latency_requests || 0);
      const label = splitCount > 0 ? 'first' : (legacyCount > 0 ? 'legacy' : 'response');
      const parts = [];
      if (avg > 0) parts.push(`${Math.round(avg)}ms ${label}`);
      if (total > 0) parts.push(`<br><span class="muted">${esc(Math.round(total))}ms total</span>`);
      if (splitCount > 0 && legacyCount > 0) parts.push(`<br><span class="muted">${esc(legacyCount)} legacy excluded</span>`);
      return parts.join('') || '-';
    }
    function renderCache(days) {
      cacheEl.innerHTML = (days || []).map(row => {
        const likely = Number(row.cache_likely_requests || 0);
        const low = Number(row.cache_low_requests || 0);
        const cacheDetails = [
          likelyUsageCacheText(row),
          nativeCacheSummary(row.nativeCache),
          likely ? `${num(low)}/${num(likely)} low in legacy estimate` : '',
        ].filter(Boolean).map(item => `<br><span class="muted">${esc(item)}</span>`).join('');
        return `<tr>
        <td>${esc(row.day)}</td>
        <td>${esc(row.logged_requests && Number(row.logged_requests) !== Number(row.requests || 0) ? `${row.requests || 0}/${row.logged_requests}` : (row.requests || 0))}</td>
        <td>${esc(num(row.input_tokens))}</td>
        <td>${esc(num(row.cached_input_tokens))}</td>
        <td>${esc(cacheUsagePercent(row.input_tokens, row.cached_input_tokens, row.nativeCache))}${cacheDetails}</td>
        <td>${latencyCell(row)}</td>
        <td class="${Number(row.errors || 0) ? 'bad' : ''}">${esc(row.errors || 0)}</td>
      </tr>`;
      }).join('') || '<tr><td colspan="7" class="muted">No Codex cache rows yet.</td></tr>';
    }
    function renderLeases(leases) {
      leaseSummary.textContent = `${(leases || []).length} active`;
      leasesEl.innerHTML = (leases || []).map(lease => `<tr>
        <td>${esc(lease.affinityKeyHash)}</td>
        <td>${esc(lease.slotIndex)}</td>
        <td>${esc(lease.accountKey)}</td>
        <td>${esc(fmtTime(lease.lastUsedAt))}</td>
        <td>${esc(fmtTime(lease.nominalExpiresAt))}</td>
      </tr>`).join('') || '<tr><td colspan="5" class="muted">No active cache leases.</td></tr>';
    }
    function showError(message) {
      errorsEl.replaceChildren();
      if (!message) return;
      const panel = document.createElement('div');
      panel.className = 'panel bad';
      panel.setAttribute('role', 'alert');
      panel.textContent = message;
      errorsEl.appendChild(panel);
    }
    function renderErrors(payload) {
      const errors = [...(payload.slotErrors || []), ...(payload.resetCreditErrors || [])];
      const telemetryErrors = payload.telemetryErrors || [];
      errorsEl.replaceChildren();
      if (telemetryErrors.length) {
        const telemetryPanel = document.createElement('div');
        telemetryPanel.className = 'panel warn';
        telemetryPanel.setAttribute('role', 'status');
        const operations = [...new Set(telemetryErrors.map(error => error.operation).filter(Boolean))];
        telemetryPanel.textContent = `Telemetry degraded${operations.length ? ` (${operations.join(', ')})` : ''}. Live usage remains available; history or cache data may be incomplete.`;
        errorsEl.appendChild(telemetryPanel);
      }
      if (!errors.length) return;
      const upstreamPanel = document.createElement('div');
      upstreamPanel.className = 'panel bad';
      upstreamPanel.setAttribute('role', 'alert');
      errors.forEach(error => {
        const line = document.createElement('div');
        line.textContent = `slot ${error.slotIndex}: ${error.message}`;
        upstreamPanel.appendChild(line);
      });
      errorsEl.appendChild(upstreamPanel);
    }
    async function fetchJson(path, options = {}) {
      const timeoutController = new AbortController();
      let timedOut = false;
      const timeout = setTimeout(() => {
        timedOut = true;
        timeoutController.abort();
      }, 5000);
      if (options.signal) {
        if (options.signal.aborted) timeoutController.abort();
        else options.signal.addEventListener('abort', () => timeoutController.abort(), { once: true });
      }
      try {
        const response = await fetch(path, { ...options, signal: timeoutController.signal });
        let payload = null;
        try { payload = await response.json(); } catch (_error) { payload = null; }
        if (!response.ok) {
          throw new Error(payload?.error?.message || `${path} HTTP ${response.status}`);
        }
        return payload;
      } catch (error) {
        if (timedOut) throw new Error('Request timed out after 5 seconds');
        throw error;
      } finally {
        clearTimeout(timeout);
      }
    }
    async function patchSlot(slotIndex, enabled) {
      await fetchJson(`/api/codex/balancer/slots/${slotIndex}`, {
        method: 'PATCH',
        headers: headers(true),
        body: JSON.stringify({ routingEnabled: enabled }),
      });
      await load();
    }
    async function forceSlot(slotIndex) {
      const payload = await fetchJson(`/api/codex/usage/slots/${slotIndex}`, {
        method: 'POST',
        headers: headers(),
      });
      renderPayload(payload);
    }
    async function patchCapacity(accountKey, capacityMultiplier, signal) {
      return fetchJson(`/api/codex/balancer/accounts/${encodeURIComponent(accountKey)}/capacity`, {
        method: 'PATCH',
        headers: headers(true),
        body: JSON.stringify({ capacityMultiplier }),
        signal,
      });
    }
    accountsEl.addEventListener('change', async event => {
      const select = event.target.closest('select[data-action="capacity"]');
      if (!select) return;
      const generation = ++loadGeneration;
      if (activeLoadController) activeLoadController.abort();
      restoreLoadControls();
      const controller = new AbortController();
      activeLoadController = controller;
      select.disabled = true;
      try {
        const payload = await patchCapacity(
          select.dataset.account,
          Number(select.value),
          controller.signal,
        );
        if (generation === loadGeneration && !controller.signal.aborted) renderPayload(payload);
      } catch (error) {
        if (generation === loadGeneration && !controller.signal.aborted) {
          showError(error.message);
          await load();
        }
      } finally {
        if (generation === loadGeneration) {
          activeLoadController = null;
          restoreLoadControls();
        }
      }
    });
    accountsEl.addEventListener('click', async event => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const originalLabel = button.textContent;
      button.disabled = true;
      button.textContent = 'Working…';
      try {
        if (button.dataset.action === 'toggle') {
          await patchSlot(button.dataset.slot, button.dataset.next === 'true');
        } else if (button.dataset.action === 'usage') {
          await forceSlot(button.dataset.slot);
        }
      } catch (error) {
        showError(error.message);
      } finally {
        button.disabled = false;
        button.textContent = originalLabel;
      }
    });
    let loadGeneration = 0;
    let activeLoadController = null;
    function restoreLoadControls() {
      const refresh = document.getElementById('refresh');
      const force = document.getElementById('force');
      refresh.disabled = false;
      refresh.textContent = 'Refresh';
      force.disabled = false;
      force.textContent = 'Refresh Live';
    }
    function renderPayload(payload) {
      const accounts = payload.accounts || [];
      const balancer = payload.balancer || {};
      const progress = payload.refreshProgress || {};
      const source = payload.source === 'persisted'
        ? `saved ${fmtTime(payload.observedAt)}`
        : (payload.source === 'memory' ? 'recent live data' : payload.source || 'live');
      let progressStatus = '';
      if (progress.status === 'discovering' && progress.totalAccounts === null) {
        progressStatus = 'Discovering accounts…';
      } else if (progress.status === 'running') {
        const pending = (progress.pendingSlotIndexes || []).map(index => index + 1);
        progressStatus = `${progress.completedAccounts} / ${progress.totalAccounts}${pending.length ? ` · pending slots ${pending.join(', ')}` : ''}`;
      } else if (progress.status === 'complete') {
        progressStatus = `complete · ${progress.completedAccounts} / ${progress.totalAccounts}`;
      } else if (progress.status === 'failed') {
        progressStatus = `failed · ${progress.completedAccounts} / ${progress.totalAccounts ?? '?'}`;
      }
      const healthSources = (payload.slots || []).map(slot => slot.healthDataSource).filter(Boolean);
      const status = [
        source,
        progressStatus,
        payload.refreshing ? 'refreshing in background' : '',
        payload.partial ? 'partial' : '',
        healthSources.length ? `slot data: ${[...new Set(healthSources)].join(', ')}` : '',
      ].filter(Boolean).join(' · ');
      usageStatus.textContent = status;
      renderAccountCards(accounts, payload.slots || balancer.slots || [], balancer.activeLeases || []);
      renderModelUsage(payload.weeklyModelUsage || {});
      renderEfficiency(payload.weeklyModelUsage || {});
      renderCache(payload.cacheDaily || []);
      renderLeases(balancer.activeLeases || []);
      renderErrors(payload);
      updateCountdowns();
    }
    async function pollRefresh(generation, signal, deadline) {
      while (generation === loadGeneration && !signal.aborted) {
        if (Date.now() >= deadline) {
          showError('UI polling stopped after 45 seconds; server refresh may still be running.');
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (generation !== loadGeneration || signal.aborted) return;
        const payload = await fetchJson('/api/codex/usage', { headers: headers(), signal: signal });
        if (generation !== loadGeneration || signal.aborted) return;
        renderPayload(payload);
        if (!payload.refreshing) return;
      }
    }
    async function load(force = false) {
      const generation = ++loadGeneration;
      if (activeLoadController) activeLoadController.abort();
      restoreLoadControls();
      const controller = new AbortController();
      activeLoadController = controller;
      const refreshButton = document.getElementById(force ? 'force' : 'refresh');
      refreshButton.disabled = true;
      refreshButton.textContent = force ? 'Refreshing live…' : 'Loading…';
      if (!accountsEl.children.length) {
        accountsEl.innerHTML = '<div class="panel skeleton" role="status" aria-busy="true">Loading saved balances…</div>';
      }
      try {
        const requestOptions = force
          ? { method: 'POST', headers: headers(), signal: controller.signal }
          : { method: 'GET', headers: headers(), signal: controller.signal };
        const payload = await fetchJson(
          force ? '/api/codex/usage/refresh' : '/api/codex/usage',
          requestOptions,
        );
        if (generation !== loadGeneration || controller.signal.aborted) return;
        renderPayload(payload);
        if (payload.refreshing) {
          await pollRefresh(generation, controller.signal, Date.now() + 45000);
        }
      } catch (error) {
        if (generation === loadGeneration && !controller.signal.aborted) showError(error.message);
      } finally {
        if (generation === loadGeneration) {
          restoreLoadControls();
        }
      }
    }
    setInterval(updateCountdowns, 1000);
    load();
