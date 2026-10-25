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
// Loaded on the first turn expansion; shared formatting comes from dashboard.js.
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
    function requestCorrelationDetail(context) {
      const correlation = context?.correlation;
      const attempts = context?.native_cache?.attempts;
      return {
        meta: `${correlation ? correlationSummary({ correlation }) : ''}${Array.isArray(attempts) ? `<span class="meta-pill">${attempts.length} native attempts recorded</span>` : ''}`,
        operation: correlation?.operation ? `<p class="muted">Client-asserted operation: ${esc(correlation.operation)}</p>` : '',
      };
    }
    function renderRequest(row) {
      const preview = requestPreview(row);
      const correlation = requestCorrelationDetail(row.context);
      const slot = row.selectedCodexSlotIndex ?? '-';
      const account = row.selectedCodexAccountKey || '';
      const total = row.totalResponseTimeMs || 0;
      const latencyKind = row.latencySource === 'legacy_response_time' ? 'legacy' : 'first';
      const cachePercent = row.context?.native_cache?.version === 2 && !row.context.native_cache.cache_read_known ? 'unknown' : tokenCachePercent(row.cachedInputTokens, row.inputTokens);
      const reasoning = row.requestedReasoningEffort || row.context?.requested_reasoning_effort || 'default';
      const tools = row.actualToolCallCount == null ? 'unknown'
        : `${row.actualToolCallCount}${row.toolTelemetryComplete === false ? '+ calls · partial' : ' calls'}`;
      const toolNames = Array.isArray(row.actualToolNames) ? row.actualToolNames : [];
      return `<details class="request${row.error ? ' error' : ''}" data-request-id="${esc(row.requestId || row.id || row.timestamp || '')}">
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
            ${correlation.meta}
            ${row.toolsAvailable ? '<span class="meta-pill">tools offered</span>' : ''}
            ${toolNames.length ? `<span class="meta-pill">called ${esc(toolNames.join(', '))}</span>` : ''}
            ${row.context?.request_trace?.delta?.phase ? `<span class="meta-pill">${esc(row.context.request_trace.delta.phase)}</span>` : ''}
          </div>
          <p class="muted">${esc(nativeCacheDetail(row.context?.native_cache))}</p>
          ${renderTraceItems(row)}
          <details class="diagnostics">
            <summary>Technical identifiers</summary>
            ${correlation.operation}
            <div class="diagnostic-list">
              <span>request ${esc(row.requestId || 'unavailable')}</span>
              <button type="button" data-copy="${esc(row.requestId || '')}">Copy request ID</button>
              <span>slot ${esc(slot)}${account ? ` · account ${esc(account)}` : ''}</span>
            </div>
          </details>
        </div>
      </details>`;
    }
    function renderRequests(target, rows) {
      const states = new Map([...target.querySelectorAll('details.request')].map(item => [
        item.dataset.requestId, [item.open, ...[...item.querySelectorAll('details')].map(detail => detail.open)],
      ]));
      stableHTML(target, `<div class="request-list">${rows.map(renderRequest).join('') || '<div class="muted">No request detail is available.</div>'}</div>`);
      target.querySelectorAll('details.request').forEach(item => {
        const state = states.get(item.dataset.requestId);
        if (!state) return;
        item.open = state[0];
        item.querySelectorAll('details').forEach((detail, index) => { detail.open = state[index + 1] || false; });
      });
    }
    window.SpartanRouteDetails = { renderRequest, renderRequests };
