    const tokenInput = document.getElementById('token');
    const accountsEl = document.getElementById('accounts');
    const glanceEl = document.getElementById('glance');
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
    function shortCountdown(value) {
      const target = deadlineMs(value);
      if (!Number.isFinite(target)) return 'unknown';
      const minutes = Math.max(0, Math.floor((target - Date.now()) / 60000));
      const days = Math.floor(minutes / 1440);
      const hours = Math.floor((minutes % 1440) / 60);
      if (days) return `${days}d${hours}h`;
      if (hours) return `${hours}h${minutes % 60}m`;
      return minutes ? `${minutes}m` : '<1m';
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
      if (document.hidden) return;
      document.querySelectorAll('[data-countdown]').forEach(element => {
        const prefix = element.getAttribute('data-countdown-prefix') || '';
        const format = element.getAttribute('data-countdown-format') === 'short' ? shortCountdown : countdownText;
        element.textContent = `${prefix}${format(element.getAttribute('data-countdown'))}`;
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
    function windowPercent(row) {
      const value = row?.usedPercent;
      return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
    }
    function observationText(row, account) {
      const source = row?.source || account.source || 'unknown source';
      const stale = row?.stale || account.stale ? 'Stale' : 'Observed';
      const observed = row?.updatedAt ? fmtTime(row.updatedAt) : 'time unknown';
      return `${stale} · ${source} · ${observed}`;
    }
    function windowUnits(row) {
      return Number.isFinite(row?.usedUnits) && Number.isFinite(row?.capacityUnits)
        ? `${fmtUnits(row.usedUnits)} / ${fmtUnits(row.capacityUnits)} units` : 'Weighted units unavailable';
    }
    function windowStaleBadge(row, account) {
      const differentSource = row?.source && row.source !== (account.source || account.weekly?.source);
      return account.stale || row?.stale || differentSource ? `<span class="pill window-stale" title="${esc(observationText(row, account))}">stale</span>` : '';
    }
    function quotaTone(value, cap, color) {
      return value !== null && Number.isFinite(cap) && cap > 0 && value >= Math.min(100, cap) ? 'var(--bad)' : color;
    }
    function balanceRing(windowName, row, secondary = false, account = {}) {
      row = row || {};
      const used = windowPercent(row);
      const percent = used === null ? 'Usage unavailable' : `${used.toFixed(1)}% raw consumed`;
      const color = secondary ? 'var(--c-5h)' : 'var(--c-week)';
      const cap = account.quotaLimits?.[secondary ? 'fiveHour' : 'weekly'];
      const tone = quotaTone(used, cap, color);
      const extras = [windowUnits(row), secondary ? '' : paceVerdict(account)].filter(Boolean).join(' · ');
      return `<div class="ring-wrap${secondary ? ' secondary' : ''}" style="--window-color:${color}" aria-label="${esc(windowName)}">
        <h4>${esc(secondary ? '5h' : 'Weekly')}</h4>
        <div class="usage-bar${used === null ? ' bar-unavailable' : ''}" style="--used:${used ?? 0}%;--tone:${tone}" aria-hidden="true"><span></span></div>
        <strong class="window-value" title="${esc(percent)}">${used === null ? '—' : `${Number(used.toFixed(1))}%`}</strong>
        <span class="window-reset" title="${esc(resetTitle(row.resetAt))}" data-countdown-format="short" data-countdown="${esc(row.resetAt || '')}">${esc(shortCountdown(row.resetAt))}</span>
        <div class="window-extra">${esc(extras)} ${windowStaleBadge(row, account)}</div>
      </div>`;
    }
    const limitDrafts = new Map();
    function limitDraft(account) {
      const key = account.accountKey;
      let draft = limitDrafts.get(key);
      if (!draft || (!draft.dirty && !draft.pending && !draft.error)) {
        const limits = account.quotaLimits || {};
        draft = { weekly: String(limits.weekly ?? ''), fiveHour: String(limits.fiveHour ?? ''), daily: String(limits.daily ?? ''), dirty: false, pending: false, error: '', invalid: {} };
        limitDrafts.set(key, draft);
      }
      return draft;
    }
    function quotaLimit(account, name, label) {
      const draft = limitDraft(account);
      const valid = /^[0-9a-f]{16}$/.test(account.accountKey || '');
      const daily = name === 'daily';
      return `<div class="quota-window">
        <label class="quota-limit">${esc(label)}${daily ? '' : ' · pause at % consumed'}
          <input type="number" min="0" max="100" step="any" inputmode="decimal" placeholder="No limit"
            aria-label="${esc(label)}${daily ? '' : ' consumed percent limit'}" data-action="limit-${name}" data-account="${esc(account.accountKey)}"
            value="${esc(draft[name])}"${valid && !draft.pending ? '' : ' disabled'}>
        </label><p class="control-window-state muted">${daily ? 'Weekly percentage points per aligned 24-hour slice · not tokens' : `${esc(windowUnits(account[name]))}`}</p></div>`;
    }
    function limitActions(account) {
      const draft = limitDraft(account);
      if (!/^[0-9a-f]{16}$/.test(account.accountKey || '')) return '';
      return `<div class="quota-actions"><button class="primary" data-action="limits" data-account="${esc(account.accountKey)}"${draft.pending ? ' disabled' : ''}>${draft.pending ? 'Saving…' : 'Save limits'}</button>
        <span class="muted">Blank = no limit · applies to routing admission</span><span class="bad quota-error" role="status">${esc(draft.error)}</span></div>`;
    }
    accountsEl.addEventListener('input', event => {
      const input = event.target.closest('input[data-action^="limit-"]');
      if (!input) return;
      const draft = limitDrafts.get(input.dataset.account);
      if (!draft || draft.pending) return;
      const name = input.dataset.action.slice(6);
      draft[name] = input.value;
      draft.invalid[name] = input.validity?.badInput === true;
      draft.dirty = true;
      draft.error = '';
    });
    async function saveLimits(button, signal) {
      const key = button.dataset.account;
      const draft = limitDrafts.get(key);
      const body = Object.fromEntries(['weekly', 'fiveHour', 'daily'].map(name => [name, draft[name].trim() === '' ? null : Number(draft[name])]));
      draft.pending = true;
      try {
        if (Object.values(draft.invalid).some(Boolean) || Object.values(body).some(value => value !== null && (!Number.isFinite(value) || value <= 0 || value > 100))) {
          throw new Error('Limits must be greater than 0 and at most 100, or blank.');
        }
        const saved = await fetchJson(`/api/codex/balancer/accounts/${encodeURIComponent(key)}/limits`, {
          method: 'PATCH', headers: headers(true), body: JSON.stringify(body), signal,
        });
        Object.assign(draft, { weekly: String(saved.quotaLimits.weekly ?? ''), fiveHour: String(saved.quotaLimits.fiveHour ?? ''), daily: String(saved.quotaLimits.daily ?? ''), dirty: false, error: '' });
      } catch (error) {
        draft.error = error.message;
        throw error;
      } finally {
        draft.pending = false;
        const card = button.closest('article.account');
        if (card) card.querySelector('.quota-error').textContent = draft.error;
      }
      return reconcileUsageAfterWrite(signal);
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
    function creditCount(credits) {
      return Number.isSafeInteger(credits?.availableCount) && credits.availableCount >= 0 ? credits.availableCount : null;
    }
    function creditDate(value) {
      const target = deadlineMs(value);
      return Number.isFinite(target) ? new Date(target).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : 'unknown';
    }
    function resetCreditStrip(credits) {
      const count = creditCount(credits);
      if (count === null || count === 0) return '';
      const items = Array.isArray(credits?.credits) ? credits.credits : [];
      return `<div class="credit-row reset-credit-strip" aria-label="Reset credits">
        <strong>⟲ ${count}</strong>
        ${items.slice(0, 4).map(item => `<span class="pill">${esc(item.status || 'unknown')} · ${esc(item.resetType || 'type unknown')} · exp ${esc(creditDate(item.expiresAt))}</span>`).join('')}
        ${items.length > 4 ? `<span class="muted">+${items.length - 4} more</span>` : ''}
        ${!items.length && count !== null ? '<span class="muted">Credit details unavailable</span>' : ''}
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
      const refreshed = slot.authLastRefreshAt ? `Last refreshed ${esc(fmtTime(slot.authLastRefreshAt))}` : 'No refresh recorded.';
      const persistence = slot.authPersistenceHealthy === false
        ? ` · persistence unavailable (${esc(slot.authErrorCode || 'auth_store_unavailable')})`
        : '';
      return `<details class="diagnostics auth-lifecycle ${slot.authPersistenceHealthy === false ? 'warn' : 'muted'}" data-slot-index="${esc(slot.slotIndex)}"><summary><strong>OAuth ${esc(source)}</strong> · ${expiry}${persistence}</summary><div>${refreshed}</div></details>`;
    }
    function renderSlotControls(slot) {
      const tone = slot.healthStatus === 'failed' ? 'bad' : (slot.healthStatus === 'unavailable' ? 'warn' : (slot.healthStatus === 'healthy' ? 'good' : 'muted'));
      return `<div class="slot-row">
        <div class="slot-row-main">
          <strong>slot ${esc(slot.slotIndex)}</strong>
          <span class="slot-state ${tone}">${esc(slotStateLine(slot))}</span>
        </div>
        <div class="slot-actions">
          <button data-action="toggle" data-slot="${esc(slot.slotIndex)}" data-next="${slot.routingEnabled ? 'false' : 'true'}" class="${slot.routingEnabled ? 'danger' : 'primary'}" aria-label="${slot.routingEnabled ? 'Disable' : 'Enable'} slot ${esc(slot.slotIndex)}">${esc(slot.routingEnabled ? 'Disable' : 'Enable')}</button>
          <button data-action="usage" data-slot="${esc(slot.slotIndex)}" aria-label="Check usage for slot ${esc(slot.slotIndex)}">Check usage</button>
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
            capacityMultiplier: slot.capacityMultiplier,
            quotaLimits: slot.quotaLimits,
            quotaPolicy: slot.quotaPolicy,
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
      const multiplier = account.capacityMultiplier === undefined ? 1 : account.capacityMultiplier;
      if (multiplier === null) return '<span class="muted">Weighted capacity unavailable</span>';
      return `<label class="plan-control">Capacity
        <select data-action="capacity" data-saved-value="${esc(multiplier)}" data-account="${esc(accountKey)}"${valid ? '' : ' disabled'}>
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
      const priority = { healthy: 0, unknown: 1, history: 1, unavailable: 2, paused: 3, failed: 4 };
      const status = enabled.reduce((current, slot) => {
        const candidate = slot.healthStatus || 'unknown';
        return (priority[candidate] || 0) > (priority[current] || 0) ? candidate : current;
      }, 'healthy');
      const affected = enabled.filter(slot => (slot.healthStatus || 'unknown') === status);
      const reasons = [...new Set(affected.flatMap(slot => slot.healthReasons || []))];
      const labels = { paused: 'Paused by limit', failed: 'Failed', unavailable: 'Unavailable', history: 'History', disabled: 'Disabled', healthy: 'Healthy', unknown: 'Unknown' };
      return {
        status,
        label: labels[status] || 'Unknown',
        reasons: reasons.length ? reasons : ['No additional health detail'],
        slotIndexes: affected.map(slot => slot.slotIndex),
      };
    }
    function dailyLimit(account) {
      const limit = account.quotaLimits?.daily ?? account.dailyBudget?.limit;
      return typeof limit === 'number' && Number.isFinite(limit) && limit > 0 && limit <= 100 ? limit : null;
    }
    function dailyConsumed(account) {
      const budget = account.dailyBudget;
      return ['ok', 'reached'].includes(budget?.status) && Number.isFinite(budget.consumed) && budget.consumed >= 0 ? budget.consumed : null;
    }
    function weeklyPace(row, stale = false) {
      const reset = deadlineMs(row?.resetAt);
      const minutes = row?.windowMinutes ?? 10080;
      if (stale || row?.stale || !Number.isFinite(reset) || reset <= Date.now() || !Number.isFinite(minutes) || minutes <= 0) return null;
      return Math.max(0, Math.min(100, 100 * (1 - (reset - Date.now()) / (minutes * 60000))));
    }
    function ringGlyph(rings, size, centerText = '') {
      const radii = { weekly: 44, today: 34, fiveHour: 24 };
      const point = (radius, percent) => {
        const angle = (percent / 100 * 360 - 90) * Math.PI / 180;
        return [50 + radius * Math.cos(angle), 50 + radius * Math.sin(angle)].map(value => value.toFixed(3));
      };
      const markup = rings.map(ring => {
        const radius = radii[ring.key];
        const known = typeof ring.value === 'number' && Number.isFinite(ring.value) && ring.value >= 0;
        const value = known ? Math.min(100, ring.value) : null;
        const cap = Number.isFinite(ring.cap) && ring.cap > 0 ? Math.min(100, ring.cap) : null;
        const tone = quotaTone(known ? ring.value : null, cap, ring.color);
        const start = point(radius - 5, cap);
        const end = point(radius + 5, cap);
        const pace = point(radius, ring.pace);
        return `<circle class="glyph-track${known ? '' : ' glyph-unknown'}" cx="50" cy="50" r="${radius}" fill="none" stroke="var(--control)" stroke-width="8"${known ? '' : ' stroke-dasharray="2 3"'}/>
          ${known ? `<circle class="glyph-arc" cx="50" cy="50" r="${radius}" fill="none" stroke="${tone}" stroke-width="8" stroke-linecap="round" pathLength="100" stroke-dasharray="${value} 100" transform="rotate(-90 50 50)"/>` : ''}
          ${cap !== null ? `<line class="glyph-stop" x1="${start[0]}" y1="${start[1]}" x2="${end[0]}" y2="${end[1]}" stroke="var(--text)" stroke-width="2"/>` : ''}
          ${Number.isFinite(ring.pace) ? `<circle class="glyph-pace" cx="${pace[0]}" cy="${pace[1]}" r="2.7" fill="var(--panel)" stroke="var(--text)" stroke-width="1.5"/>` : ''}`;
      }).join('');
      return `<svg class="ring-glyph" width="${size}" height="${size}" viewBox="0 0 100 100" role="img" aria-label="${esc(rings.map(ring => ring.label).join('; '))}">${markup}${size >= 72 ? `<text class="glyph-center" x="50" y="52" text-anchor="middle" dominant-baseline="middle">${esc(centerText)}</text>` : ''}</svg>`;
    }
    function accountGlyph(account) {
      const limits = account.quotaLimits || {};
      const weekly = windowPercent(account.weekly);
      const fiveHour = windowPercent(account.fiveHour);
      const daily = dailyLimit(account);
      const consumed = dailyConsumed(account);
      const label = (name, value, cap) => `${name} ${value === null ? 'usage unknown' : `${value}% used`}${cap != null ? `, stop at ${cap}%` : ''}${account.stale ? ', stale' : ''}`;
      const rings = [{ key: 'weekly', value: weekly, cap: limits.weekly, pace: weeklyPace(account.weekly, account.stale), color: 'var(--c-week)', label: label('Weekly', weekly, limits.weekly) }];
      if (daily !== null) rings.push({ key: 'today', value: consumed === null ? null : consumed / daily * 100, cap: 100, color: 'var(--c-day)', label: `today ${consumed === null ? 'unknown' : consumed} of ${daily} points` });
      rings.push({ key: 'fiveHour', value: fiveHour, cap: limits.fiveHour, color: 'var(--c-5h)', label: label('5-hour', fiveHour, limits.fiveHour) });
      return ringGlyph(rings, 112, weekly === null ? '—' : `${Math.round(weekly)}%`);
    }
    function meterWindow(label, name, row) {
      const used = windowPercent(row);
      return `<span class="meter m-${name}">${label} ${used === null ? '?' : `${Math.round(used)}%`} <span title="${esc(resetTitle(row?.resetAt))}" data-countdown-format="short" data-countdown="${esc(row?.resetAt || '')}">${esc(shortCountdown(row?.resetAt))}</span></span>`;
    }
    function dailyWindow(account) {
      const limit = dailyLimit(account);
      if (limit === null) return '';
      const consumed = dailyConsumed(account);
      const end = account.dailyBudget?.sliceEnd;
      const ratio = consumed === null ? null : Math.min(100, consumed / limit * 100);
      return `<div class="ring-wrap today-window" style="--window-color:var(--c-day)">
        <h4>Today</h4>
        <div class="usage-bar${ratio === null ? ' bar-unavailable' : ''}" style="--used:${ratio ?? 0}%;--tone:${account.dailyBudget?.status === 'reached' ? 'var(--bad)' : 'var(--c-day)'}" aria-hidden="true"><span></span></div>
        <strong class="window-value" title="Weekly percentage points consumed in this daily slice">${consumed === null ? '?' : consumed.toFixed(1)}/${limit} pts</strong>
        <span class="window-reset" title="${esc(resetTitle(end))}" data-countdown-format="short" data-countdown="${esc(end || '')}">${esc(shortCountdown(end))}</span>
        <div class="window-extra">slice resets <span data-countdown-format="short" data-countdown="${esc(end || '')}">${esc(shortCountdown(end))}</span>${account.dailyBudget?.status === 'reached' ? ' <span class="pill warn">reached</span>' : ''}</div>
      </div>`;
    }
    function paceVerdict(account) {
      const used = windowPercent(account.weekly);
      const pace = weeklyPace(account.weekly, account.stale);
      if (used === null || pace === null) return 'pace unknown';
      const delta = Math.round(used - pace);
      return delta > 0 ? `+${delta} pts ahead of pace` : delta < 0 ? `${-delta} pts under pace` : 'On pace';
    }
    function renderGlance(accounts, slots) {
      const cards = mergeAccountCards(accounts, slots);
      const fleetKnown = Array.isArray(accounts);
      const counts = cards.map(account => creditCount(account.resetCredits));
      const total = counts.reduce((sum, count) => sum + (count ?? 0), 0);
      const unknown = !fleetKnown || counts.some(count => count === null);
      const chips = cards.filter(account => creditCount(account.resetCredits) > 0).map(account => {
        const available = (account.resetCredits.credits || []).filter(credit => credit.status === 'available' && Number.isFinite(deadlineMs(credit.expiresAt))).sort((a, b) => deadlineMs(a.expiresAt) - deadlineMs(b.expiresAt));
        return `<a class="pill credit-link" href="#acct-${esc(accountCardKey(account))}">…${esc(account.accountKey.slice(-4))} ×${creditCount(account.resetCredits)} · exp ${esc(creditDate(available[0]?.expiresAt))}</a>`;
      }).join('');
      const health = cards.map(accountHealth);
      const routing = cards.filter(account => account.slots.some(slot => slot.routingEnabled && slot.healthStatus === 'healthy')).length;
      const nextReset = name => cards.filter(account => deadlineMs(account[name]?.resetAt) > Date.now()).sort((a, b) => deadlineMs(a[name].resetAt) - deadlineMs(b[name].resetAt))[0];
      const next = nextReset('fiveHour');
      const week = nextReset('weekly');
      const reset = (account, name) => account ? `<span title="${esc(resetTitle(account[name].resetAt))}" data-countdown-format="short" data-countdown="${esc(account[name].resetAt)}">${esc(shortCountdown(account[name].resetAt))}</span> <span class="reset-account">…${esc(account.accountKey.slice(-4))}</span>` : 'unknown';
      stableHTML(glanceEl, `<div class="glance-tile tile-credits"><div class="tile-label"><span class="label-wide">Resets available</span><span class="label-compact">Resets</span></div><div class="tile-value">${fleetKnown ? `${total}${unknown ? '+' : ''}` : '?'}</div><div class="tile-detail">${unknown ? 'some accounts unknown' : total === 0 ? 'None banked' : 'Banked reset credits'}</div><div class="credit-links">${chips}</div></div>
        <div class="glance-tile tile-routing"><div class="tile-label">Routing</div><div class="tile-value">${fleetKnown ? `${routing}/${cards.length}` : '?'}</div><div class="tile-detail">${health.filter(row => row.status === 'paused').length} paused · ${health.filter(row => row.status === 'disabled').length} disabled</div></div>
        <div class="glance-tile tile-next"><div class="tile-label">Next reset</div><div class="tile-value"><span class="reset-kind">5h</span> ${reset(next, 'fiveHour')}</div><div class="tile-detail"><span class="reset-kind">week</span> ${reset(week, 'weekly')}</div></div>`);
    }
    const renderedMarkup = new WeakMap();
    function stableHTML(element, markup) {
      if (renderedMarkup.get(element) === markup) return false;
      element.innerHTML = markup;
      renderedMarkup.set(element, markup);
      return true;
    }
    const cardSignatures = new WeakMap();
    function accountCardKey(account) {
      return account.accountKey && account.accountKey !== 'unknown' ? account.accountKey : `slots:${(account.slotIndexes || []).join(',')}`;
    }
    function disclosureKey(item) {
      const kind = item.className.split(' ').find(name => ['auth-lifecycle', 'account-detail', 'account-controls'].includes(name));
      return JSON.stringify([kind, item.dataset.accountKey, item.dataset.slotIndex]);
    }
    const accountDisclosures = 'details.auth-lifecycle, details.account-detail, details.account-controls';
    function captureAccountFocus() {
      const element = document.activeElement;
      const card = element?.closest?.('article.account');
      return {
        element, accountKey: card?.dataset.accountKey,
        summaryKey: element?.matches?.('summary') && card ? disclosureKey(element.closest('details')) : null,
        control: element?.dataset?.action ? { ...element.dataset, draft: element.value !== element.dataset.savedValue, value: element.value } : null,
      };
    }
    function restoreAccountFocus({ element, accountKey, summaryKey, control }) {
      if (summaryKey) {
        const details = [...accountsEl.querySelectorAll(accountDisclosures)].find(item => disclosureKey(item) === summaryKey);
        details?.querySelector('summary')?.focus({ preventScroll: true });
        return;
      }
      if (element?.isConnected) {
        if (document.activeElement !== element) element.focus({ preventScroll: true });
        return;
      }
      if (!accountKey || !control) return;
      const card = [...accountsEl.querySelectorAll('article.account')].find(item => item.dataset.accountKey === accountKey);
      const replacement = [...(card?.querySelectorAll('[data-action]') || [])].find(item => item.dataset.action === control.action
        && item.dataset.slot === control.slot && item.dataset.account === control.account);
      if (!replacement || replacement.disabled) return;
      if (control.action === 'capacity' && control.draft) replacement.value = control.value;
      replacement.focus({ preventScroll: true });
    }
    function reconcileAccountCards(markup, cards, leases) {
      const disclosures = new Map([...accountsEl.querySelectorAll(accountDisclosures)].map(item => [disclosureKey(item), item.open]));
      const focus = captureAccountFocus();
      const previous = new Map([...accountsEl.querySelectorAll('article.account')].map(item => [item.dataset.accountKey, item]));
      const template = document.createElement('template');
      template.innerHTML = markup;
      template.content.querySelectorAll('article.account').forEach((next, index) => {
        const card = cards[index];
        const relevantLeases = leases.filter(lease => lease.accountKey === card.accountKey || (card.slotIndexes || []).includes(Number(lease.slotIndex)));
        const signature = JSON.stringify([card, relevantLeases]);
        const old = previous.get(next.dataset.accountKey);
        if (old && cardSignatures.get(old) === signature) next.replaceWith(old);
        else cardSignatures.set(next, signature);
      });
      accountsEl.replaceChildren(...template.content.childNodes);
      // Native disclosures must be open before a replacement descendant receives focus.
      accountsEl.querySelectorAll(accountDisclosures).forEach(item => { item.open = disclosures.get(disclosureKey(item)) || false; });
      restoreAccountFocus(focus);
    }
    function accountCardHeader(account) {
      const count = creditCount(account.resetCredits);
      const creditLabel = count === null ? 'Reset credits unknown' : `${count} reset credits`;
      const daily = dailyLimit(account);
      const consumed = dailyConsumed(account);
      return `<header class="account-strip">
            ${accountGlyph(account)}
            <div class="account-main">
              <div class="account-line"><h3 class="account-name" title="${esc(account.accountKey)}">${esc(account.accountKey || 'unknown')}</h3><span class="pill health-badge"></span><span class="credit-badge" aria-label="${creditLabel}" title="${creditLabel}">⟲ ${count ?? '?'}</span></div>
              <div class="meter-line">${meterWindow('W', 'week', account.weekly)}${daily !== null ? `<span class="meter m-day">D ${consumed ?? '?'}/${daily}</span>` : ''}${meterWindow('5h', '5h', account.fiveHour)}</div>
              <div class="account-health" role="status"></div>
              ${resetCreditStrip(account.resetCredits)}
            </div>
          </header>`;
    }
    let accountSignature = null;
    function renderAccountCards(accounts, slots = [], leases = []) {
      const signature = JSON.stringify([accounts, slots, leases]);
      if (signature === accountSignature) { updateCountdowns(); return; }
      accountSignature = signature;
      const cards = mergeAccountCards(accounts, slots);
      const healthRows = [];
      reconcileAccountCards(cards.map(account => {
        const slotsHtml = (account.slots || []).map(renderSlotControls).join('');
        const authHtml = (account.slots || []).map(slot => `<div class="slot-diagnostics"><strong>slot ${esc(slot.slotIndex)}</strong><p class="muted">${esc(slot.accountKey || 'unknown')} · ${esc(slot.slotPath || slot.slotPathHash || 'no path')}</p>${authLifecycle(slot)}</div>`).join('');
        const health = accountHealth(account);
        healthRows.push(health);
        return `<article class="account health-${health.status}${account.slotOnly ? ' slot-only' : ''}" id="acct-${esc(accountCardKey(account))}" data-account-key="${esc(accountCardKey(account))}" data-reading-stale="${Boolean(account.stale || account.weekly?.stale || account.fiveHour?.stale)}">
          ${accountCardHeader(account)}
          <div class="window-grid">
            ${balanceRing('Weekly usage', account.weekly, false, account)}
            ${dailyWindow(account)}
            ${balanceRing('Five-hour usage', account.fiveHour, true, account)}
          </div>
          <details class="account-controls" data-account-key="${esc(accountCardKey(account))}">
            <summary><span class="controls-label">Limits &amp; controls</span><span class="observation" title="${esc(observationText(account.weekly, account))}">${esc(observationText(account.weekly, account))}</span><span class="detail-source">${sourceBadge(account)}</span></summary>
            <div class="control-fields">
              ${quotaLimit(account, 'weekly', 'Weekly usage')}
              ${quotaLimit(account, 'fiveHour', 'Five-hour usage')}
              ${quotaLimit(account, 'daily', 'Daily budget (pts of weekly)')}
              ${planControl(account)}
            </div>
            ${limitActions(account)}
            <section class="slot-deck" aria-label="Slot routing controls">
              <h4>Routing slots <span>${(account.slots || []).length}</span></h4>
              ${slotsHtml || '<p class="muted">No configured slot metadata. Account limits remain available when identity is known.</p>'}
            </section>
          <details class="account-detail" data-account-key="${esc(accountCardKey(account))}">
            <summary>Account details <span class="detail-source">${sourceBadge(account)}</span></summary>
            <p class="muted">Slots ${(account.slotIndexes || []).map(esc).join(', ') || '-'} · ${esc(slotPathText(account.slotPaths))}</p>
            ${activeLeaseStrip(account, leases)}
            ${authHtml}
          </details>
          </details>
        </article>`;
      }).join('') || '<div class="panel muted">No usage telemetry yet.</div>', cards, leases);
      accountsEl.querySelectorAll('article.account').forEach((element, index) => {
        const health = healthRows[index];
        const staleText = element.getAttribute('data-reading-stale') === 'true' ? 'Stale usage · ' : '';
        const slotsText = health.slotIndexes.length ? ` · slots ${health.slotIndexes.join(', ')}` : '';
        const summary = health.status === 'healthy' || health.status === 'disabled'
          ? `${health.label}${slotsText}` : `${health.label}${slotsText}: ${health.reasons.join('; ')}`;
        element.querySelector('.health-badge').textContent = health.label;
        element.querySelector('.account-health').textContent = staleText + summary;
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
        stableHTML(modelUsageEl, `<tr><td colspan="6" class="muted">${esc(reasonLabels[usage.unavailableReason] || 'Estimate unavailable.')}</td></tr>`);
        return;
      }
      const coverage = [
        `${num(usage.sampleTokens)} calibration tokens`,
        `${num(usage.sampleAccounts)} accounts`,
        usage.excludedAccountRequests ? `${num(usage.excludedAccountRequests)} missing-usage rows excluded` : '',
        usage.unattributedRequests ? `${num(usage.unattributedRequests)} unattributed rows excluded` : '',
      ].filter(Boolean);
      modelUsageMeta.textContent = `${coverage.join(' · ')}${usage.observedAt ? ` · as of ${fmtTime(usage.observedAt)}` : ''}`;
      stableHTML(modelUsageEl, models.map(row => `<tr>
        <td>${esc(row.model)}</td>
        <td>${esc(num(row.requests))}</td>
        <td title="input ${esc(num(row.inputTokens))} · output ${esc(num(row.outputTokens))}">${esc(num(row.totalTokens))}</td>
        <td><strong>${esc(fmtUnits(row.expectedUnits))} units</strong></td>
        <td>${esc(fmtUnits(row.x1EquivalentPercent))}%</td>
        <td>${esc(fmtUnits(row.x5EquivalentPercent))}%</td>
      </tr>`).join('') || '<tr><td colspan="6" class="muted">No model usage in the current weekly window.</td></tr>');
    }
    function renderEfficiency(usage = {}) {
      const rows = Array.isArray(usage.efficiency) ? usage.efficiency : [];
      efficiencyMeta.textContent = usage.available
        ? `${rows.length} groups · current weekly window · account-weighted calibration`
        : 'Estimate unavailable';
      if (usage.unavailableReason === 'telemetry_unavailable' || usage.unavailableReason === 'optional_telemetry_disabled') {
        stableHTML(efficiencyEl, '<tr><td colspan="6" class="muted">Historical analytics unavailable.</td></tr>');
        return;
      }
      stableHTML(efficiencyEl, rows.map(row => {
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
      }).join('') || '<tr><td colspan="6" class="muted">No model and reasoning usage in the current weekly window.</td></tr>');
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
    function renderCache(days = []) {
      if (days === null) {
        stableHTML(cacheEl, '<tr><td colspan="7" class="muted">Historical cache analytics unavailable.</td></tr>');
        return;
      }
      stableHTML(cacheEl, (days || []).map(row => {
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
      }).join('') || '<tr><td colspan="7" class="muted">No Codex cache rows yet.</td></tr>');
    }
    function renderLeases(leases) {
      leaseSummary.textContent = `${(leases || []).length} active`;
      stableHTML(leasesEl, (leases || []).map(lease => `<tr>
        <td>${esc(lease.affinityKeyHash)}</td>
        <td>${esc(lease.slotIndex)}</td>
        <td>${esc(lease.accountKey)}</td>
        <td>${esc(fmtTime(lease.lastUsedAt))}</td>
        <td>${esc(fmtTime(lease.nominalExpiresAt))}</td>
      </tr>`).join('') || '<tr><td colspan="5" class="muted">No active cache leases.</td></tr>');
    }
    function showError(message) {
      previousErrors = null;
      errorsEl.replaceChildren();
      if (!message) return;
      const panel = document.createElement('div');
      panel.className = 'panel bad';
      panel.setAttribute('role', 'alert');
      panel.textContent = message;
      errorsEl.appendChild(panel);
    }
    let previousErrors = null;
    function renderErrors(payload) {
      const signature = JSON.stringify([payload.slotErrors, payload.resetCreditErrors, payload.telemetryErrors]);
      if (signature === previousErrors) return;
      previousErrors = signature;
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
    async function fetchJson(path, options = {}, retry = true) {
      const { timeoutMs = 5000, ...fetchOptions } = options;
      const timeoutController = new AbortController();
      let timedOut = false;
      const timeout = setTimeout(() => {
        timedOut = true;
        timeoutController.abort();
      }, timeoutMs);
      const abort = () => timeoutController.abort();
      if (options.signal?.aborted) abort();
      else options.signal?.addEventListener('abort', abort, { once: true });
      try {
        const response = await fetch(path, { ...fetchOptions, signal: timeoutController.signal });
        let payload = null;
        try { payload = await response.json(); } catch (_error) { payload = null; }
        if (!response.ok) {
          const error = new Error(payload?.error?.message || `${path} HTTP ${response.status}`);
          error.status = response.status;
          error.transient = response.status >= 500;
          throw error;
        }
        return payload;
      } catch (error) {
        if (retry && (!options.method || options.method === 'GET') && !options.signal?.aborted
            && (timedOut || error.transient || error instanceof TypeError)) {
          return fetchJson(path, options, false);
        }
        if (timedOut) throw new Error(`Request timed out after ${timeoutMs / 1000} seconds.${options.method && options.method !== 'GET' ? ' The server operation may continue; refresh to check current state.' : ' Refresh to retry.'}`);
        throw error;
      } finally {
        clearTimeout(timeout);
        options.signal?.removeEventListener('abort', abort);
      }
    }
    async function patchSlot(slotIndex, enabled, signal) {
      await fetchJson(`/api/codex/balancer/slots/${slotIndex}`, {
        method: 'PATCH',
        headers: headers(true),
        body: JSON.stringify({ routingEnabled: enabled }),
        signal,
      });
    }
    async function forceSlot(slotIndex, signal) {
      const payload = await fetchJson(`/api/codex/usage/slots/${slotIndex}`, {
        method: 'POST',
        timeoutMs: 60000,
        headers: headers(),
        signal,
      });
      return payload;
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
      if (mutationPending) { select.value = select.dataset.savedValue; return; }
      setMutationPending(true);
      const generation = ++loadGeneration;
      if (activeLoadController) activeLoadController.abort();
      restoreLoadControls();
      const controller = new AbortController();
      activeLoadController = controller;
      select.disabled = true;
      clearTimeout(pollTimer);
      try {
        const payload = await patchCapacity(
          select.dataset.account,
          Number(select.value),
          controller.signal,
        );
        if (generation === loadGeneration && !controller.signal.aborted) renderPayload(payload);
      } catch (error) {
        if (generation === loadGeneration && !controller.signal.aborted) {
          select.value = select.dataset.savedValue;
          accountSignature = null;
          showActionFailure(error);
        }
      } finally {
        setMutationPending(false);
        select.disabled = false;
        if (generation === loadGeneration) {
          activeLoadController = null;
          restoreLoadControls();
          schedulePoll();
        }
      }
    });
    async function reconcileUsageAfterWrite(signal) {
      try {
        return await fetchJson('/api/codex/usage', { headers: headers(), signal });
      } catch (error) {
        // The write succeeded; failure to read back is not a rejected write.
        error.snapshotReadFailed = true;
        throw error;
      }
    }
    async function accountAction(button, signal) {
      if (button.dataset.action === 'limits') return saveLimits(button, signal);
      if (button.dataset.action === 'usage') return forceSlot(button.dataset.slot, signal);
      if (button.dataset.action === 'toggle') {
        await patchSlot(button.dataset.slot, button.dataset.next === 'true', signal);
        if (!signal.aborted) return reconcileUsageAfterWrite(signal);
      }
      return null;
    }
    accountsEl.addEventListener('click', async event => {
      const button = event.target.closest('button[data-action]');
      if (!button || mutationPending) return;
      setMutationPending(true);
      clearTimeout(pollTimer);
      const generation = ++loadGeneration;
      if (activeLoadController) activeLoadController.abort();
      restoreLoadControls();
      const controller = new AbortController();
      activeLoadController = controller;
      const originalLabel = button.textContent;
      button.disabled = true;
      button.textContent = 'Working…';
      try {
        const payload = await accountAction(button, controller.signal);
        if (payload && generation === loadGeneration && !controller.signal.aborted) renderPayload(payload);
      } catch (error) {
        if (generation === loadGeneration && !controller.signal.aborted) showActionFailure(error);
      } finally {
        setMutationPending(false);
        button.disabled = false;
        button.textContent = originalLabel;
        if (generation === loadGeneration) {
          activeLoadController = null;
          restoreLoadControls();
          schedulePoll();
        }
      }
    });
    let loadGeneration = 0;
    let activeLoadController = null;
    let mutationPending = false;
    const mutationControls = new Map();
    function setMutationPending(pending) {
      mutationPending = pending;
      if (pending) {
        for (const control of accountsEl.querySelectorAll('button[data-action], select[data-action], input[data-action]')) {
          mutationControls.set(control, control.disabled);
          control.disabled = true;
        }
      } else {
        for (const [control, disabled] of mutationControls) control.disabled = disabled;
        mutationControls.clear();
      }
    }
    let pollTimer = null;
    let renderedCredential = tokenInput.value.trim();
    let hasPayload = false;
    let optionalTelemetryEnabled = true;
    let glanceAccounts = null;
    let glanceSlots = [];
    function clearAuthenticatedData() {
      for (const element of [accountsEl, modelUsageEl, efficiencyEl, cacheEl, leasesEl]) {
        element.replaceChildren();
        renderedMarkup.delete(element);
      }
      errorsEl.replaceChildren();
      glanceEl.replaceChildren();
      renderedMarkup.delete(glanceEl);
      glanceAccounts = null;
      glanceSlots = [];
      limitDrafts.clear();
      previousErrors = null;
      modelUsageMeta.textContent = '';
      efficiencyMeta.textContent = '';
      leaseSummary.textContent = '';
      accountSignature = null;
      accountsEl.setAttribute('data-stale', 'false');
      glanceEl.setAttribute('data-stale', 'false');
      hasPayload = false;
    }
    function schedulePoll() {
      clearTimeout(pollTimer);
      if (optionalTelemetryEnabled && !document.hidden && !mutationPending) pollTimer = setTimeout(() => load(false, true), 15000);
    }
    document.addEventListener('visibilitychange', () => {
      clearTimeout(pollTimer);
      if (!document.hidden && !mutationPending) {
        updateCountdowns();
        if (optionalTelemetryEnabled) load(false, true);
      }
    });
    tokenInput.addEventListener('input', () => {
      ++loadGeneration;
      activeLoadController?.abort();
      clearTimeout(pollTimer);
      restoreLoadControls();
      clearAuthenticatedData();
      usageStatus.textContent = 'Use token to load this account.';
    });
    function restoreLoadControls() {
      const refresh = document.getElementById('refresh');
      const force = document.getElementById('force');
      refresh.disabled = false;
      refresh.textContent = 'Refresh';
      force.disabled = false;
      force.textContent = 'Refresh Live';
    }
    function renderPayload(payload) {
      optionalTelemetryEnabled = payload.optionalTelemetryEnabled !== false;
      accountsEl.setAttribute('data-stale', 'false');
      glanceEl.setAttribute('data-stale', 'false');
      hasPayload = true;
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
      glanceAccounts = accounts;
      glanceSlots = payload.slots || balancer.slots || [];
      renderGlance(glanceAccounts, glanceSlots);
      renderAccountCards(accounts, glanceSlots, balancer.activeLeases || []);
      renderModelUsage(payload.weeklyModelUsage || {});
      renderEfficiency(payload.weeklyModelUsage || {});
      renderCache(payload.cacheDaily);
      renderLeases(balancer.activeLeases || []);
      renderErrors(payload);
      updateCountdowns();
    }
    async function pollRefresh(generation, signal, deadline) {
      while (generation === loadGeneration && !signal.aborted) {
        if (document.hidden) return;
        if (Date.now() >= deadline) {
          showError('UI polling stopped after 45 seconds; server refresh may still be running.');
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (document.hidden || generation !== loadGeneration || signal.aborted) return;
        const payload = await fetchJson('/api/codex/usage', { headers: headers(), signal: signal });
        if (generation !== loadGeneration || signal.aborted) return;
        renderPayload(payload);
        const progress = payload.refreshProgress?.status;
        if (!payload.refreshing && progress !== 'discovering' && progress !== 'running') return;
      }
    }
    function showActionFailure(error) {
      if (error.snapshotReadFailed || error.status === 401 || error.status === 403) showReadFailure(error);
      else showError(error.message);
    }
    function showReadFailure(error) {
      if (error.status === 401 || error.status === 403) {
        clearAuthenticatedData();
        document.getElementById('dashboard-access').open = true;
      }
      showError(error.message);
      accountsEl.setAttribute('data-stale', 'true');
      glanceEl.setAttribute('data-stale', 'true');
      usageStatus.textContent = hasPayload ? 'Update failed · last-good readings are stale' : 'Balances unavailable. Refresh to retry.';
      if (!hasPayload) stableHTML(accountsEl, '<div class="panel muted" role="status">Balances unavailable. Refresh to retry.</div>');
    }
    function loadBlocked(force) {
      return mutationPending || (force && document.getElementById('force').disabled);
    }
    function shouldPollRefresh(payload, force) {
      return (force || optionalTelemetryEnabled) && (payload.refreshing || ['discovering', 'running'].includes(payload.refreshProgress?.status));
    }
    async function load(force = false, background = false) {
      if (loadBlocked(force)) return;
      clearTimeout(pollTimer);
      if (renderedCredential !== tokenInput.value.trim()) {
        renderedCredential = tokenInput.value.trim();
        clearAuthenticatedData();
      }
      const generation = ++loadGeneration;
      if (activeLoadController) activeLoadController.abort();
      restoreLoadControls();
      const controller = new AbortController();
      activeLoadController = controller;
      const refreshButton = document.getElementById(force ? 'force' : 'refresh');
      if (!background) {
        refreshButton.disabled = true;
        refreshButton.textContent = force ? 'Refreshing live…' : 'Loading…';
      }
      if (!accountsEl.children.length) {
        stableHTML(accountsEl, '<div class="panel skeleton" role="status" aria-busy="true">Loading saved balances…</div>');
      }
      try {
        const requestOptions = force
          ? { method: 'POST', headers: headers(), signal: controller.signal }
          : { method: 'GET', headers: headers(), signal: controller.signal };
        const usageRequest = fetchJson(
          force ? '/api/codex/usage/refresh' : '/api/codex/usage',
          requestOptions,
        );
        // Independent publication: a missing or failed relay never blocks Codex readings.
        const payload = await usageRequest;
        if (generation !== loadGeneration || controller.signal.aborted) return;
        renderPayload(payload);
        if (shouldPollRefresh(payload, force)) {
          await pollRefresh(generation, controller.signal, Date.now() + 45000);
        }
      } catch (error) {
        if (generation === loadGeneration && !controller.signal.aborted) {
          showReadFailure(error);
        }
      } finally {
        if (generation === loadGeneration) {
          restoreLoadControls();
          activeLoadController = null;
          schedulePoll();
        }
      }
    }
    setInterval(updateCountdowns, 1000);
    load();
