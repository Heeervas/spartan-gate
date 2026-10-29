    const tokenInput = document.getElementById('token');
    const tiersEl = document.getElementById('tiers');
    const statusEl = document.getElementById('status');
    const errorEl = document.getElementById('error');
    const successEl = document.getElementById('success');
    const modeWarningEl = document.getElementById('mode-warning');
    const dirtyEl = document.getElementById('dirty');
    const saveButton = document.getElementById('save');
    const resetButton = document.getElementById('reset');
    const limitationsEl = document.getElementById('limitations');

    let documentState = null;
    let etag = null;
    let draft = {};
    let dirty = false;
    let busy = false;
    let routingLoaded = false;
    let generation = 0;
    let variantState = null;
    let variantEtag = null;
    let variantDraft = [];
    let variantDirty = false;
    let variantBusy = false;
    let variantsLoaded = false;
    const variantsEl = document.getElementById('variants');
    const variantStatus = document.getElementById('variant-status');
    const variantError = document.getElementById('variant-error');
    const variantSuccess = document.getElementById('variant-success');
    const variantWarning = document.getElementById('variant-warning');
    const variantSave = document.getElementById('variant-save');
    const variantAdd = document.getElementById('variant-add');
    const variantDirtyEl = document.getElementById('variant-dirty');
    const providersEl = document.getElementById('providers');
    const providerStatus = document.getElementById('provider-status');
    const providerError = document.getElementById('provider-error');
    const providerSuccess = document.getElementById('provider-success');
    const providerSave = document.getElementById('provider-save');
    const providerDirtyEl = document.getElementById('provider-dirty');
    let providerState = null;
    let providerEtag = null;
    let providerDraft = null;
    let providersLoaded = false;
    let providerBusy = false;
    let providerDirty = false;
    const providerModelInputDrafts = {};

    tokenInput.value = sessionStorage.getItem('spartanRouteToken') || '';

    function headers(json = false) {
      const token = tokenInput.value.trim();
      const result = token ? { Authorization: `Bearer ${token}` } : {};
      if (json) result['Content-Type'] = 'application/json';
      return result;
    }

    function setMessage(element, message) {
      element.textContent = message || '';
      element.classList.toggle('hidden', !message);
    }

    async function request(path, options = {}) {
      const controller = new AbortController();
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 5000);
      try {
        const response = await fetch(path, { ...options, signal: controller.signal });
        let payload = null;
        try { payload = await response.json(); } catch (_error) { payload = null; }
        if (!response.ok) {
          const message = payload?.error?.message || `${path} HTTP ${response.status}`;
          const error = new Error(message);
          error.status = response.status;
          error.code = payload?.error?.code || 'request_failed';
          error.etag = response.headers.get('ETag');
          throw error;
        }
        return { payload, etag: response.headers.get('ETag') || payload?.etag || null };
      } catch (error) {
        if (timedOut) throw new Error('Request timed out after 5 seconds. Reload before retrying; your edits are preserved.');
        throw error;
      } finally {
        clearTimeout(timer);
      }
    }

    function markDirty(next) {
      dirty = next;
      dirtyEl.textContent = dirty ? 'Unsaved changes' : '';
      dirtyEl.className = dirty ? 'dirty' : 'muted';
      saveButton.disabled = busy || !routingLoaded || !dirty;
    }

    function eligibleModels() {
      return (documentState?.models || [])
        .filter(model => model.eligible)
        .sort((left, right) => left.id.localeCompare(right.id));
    }

    function addText(parent, className, text) {
      const node = document.createElement('div');
      node.className = className;
      node.textContent = text;
      parent.appendChild(node);
      return node;
    }

    function render() {
      tiersEl.replaceChildren();
      limitationsEl.replaceChildren();
      const models = eligibleModels();
      const readiness = new Map((documentState?.models || []).map(model => [model.id, model.readiness]));

      for (const tier of documentState?.editableTiers || []) {
        const row = document.createElement('div');
        row.className = 'tier-row';

        const identity = document.createElement('div');
        identity.className = 'tier-meta';
        const heading = document.createElement('h3');
        heading.textContent = tier.label;
        identity.appendChild(heading);
        addText(identity, 'muted', tier.rule);

        const control = document.createElement('label');
        control.className = 'tier-meta';
        const controlLabel = document.createElement('span');
        controlLabel.className = 'label';
        controlLabel.textContent = 'Effective model';
        const select = document.createElement('select');
        select.dataset.tier = tier.id;
        select.setAttribute('aria-label', `${tier.label} model`);
        for (const model of models) {
          const option = document.createElement('option');
          option.value = model.id;
          option.textContent = `${model.id} · ${model.provider}`;
          select.appendChild(option);
        }
        if (!models.some(model => model.id === draft[tier.id])) {
          const option = document.createElement('option');
          option.value = draft[tier.id] || '';
          option.textContent = `${draft[tier.id] || 'Unavailable'} · not eligible to save`;
          option.disabled = true;
          select.prepend(option);
        }
        select.value = draft[tier.id] || '';
        select.disabled = busy || !routingLoaded;
        select.addEventListener('change', () => {
          setMessage(successEl, '');
          draft[tier.id] = select.value;
          markDirty(JSON.stringify(draft) !== JSON.stringify(documentState.effective));
          render();
        });
        control.append(controlLabel, select);

        const metadata = document.createElement('div');
        metadata.className = 'tier-meta';
        addText(metadata, 'muted', `File default: ${documentState.fileDefaults[tier.id]}`);
        addText(metadata, 'source', `Source: ${documentState.sources[tier.id]}`);
        const ready = readiness.get(draft[tier.id]) || 'unknown';
        addText(metadata, ready === 'configured' ? 'readiness' : 'readiness warn', `Readiness: ${ready.replaceAll('_', ' ')}`);

        row.append(identity, control, metadata);
        tiersEl.appendChild(row);
      }

      for (const limitation of documentState?.limitations || []) {
        const item = document.createElement('li');
        item.textContent = limitation;
        limitationsEl.appendChild(item);
      }

      tiersEl.setAttribute('aria-busy', busy ? 'true' : 'false');
      resetButton.disabled = busy || !routingLoaded || !Object.keys(documentState?.overrides || {}).length;
      markDirty(dirty);
    }

    function renderDocument(payload, nextEtag) {
      routingLoaded = true;
      documentState = payload;
      etag = nextEtag;
      draft = { ...payload.effective };
      dirty = false;
      const mode = !payload.enabled
        ? 'Routing is disabled; auto requests currently use the baseline model.'
        : payload.dryRun
          ? 'Dry-run mode is active; saved choices are recorded but not used upstream.'
          : '';
      setMessage(modeWarningEl, [mode, payload.warning].filter(Boolean).join(' '));
      setMessage(errorEl, '');
      statusEl.textContent = payload.updatedAt
        ? `Runtime override · ${new Date(payload.updatedAt).toLocaleString()}`
        : 'File defaults';
      render();
    }

    async function loadRouting(loadGeneration) {
      routingLoaded = false;
      busy = true;
      statusEl.textContent = 'Loading…';
      setMessage(successEl, '');
      setMessage(errorEl, '');
      if (documentState) render();
      try {
        const result = await request('/api/config/routing', { headers: headers() });
        if (loadGeneration !== generation) return;
        renderDocument(result.payload, result.etag);
      } catch (error) {
        if (loadGeneration !== generation) return;
        statusEl.textContent = '';
        setMessage(errorEl, error.message);
      } finally {
        if (loadGeneration !== generation) return;
        busy = false;
        if (documentState) render();
      }
    }

    async function save() {
      if (!dirty || busy || !routingLoaded) return;
      const loadGeneration = generation;
      busy = true;
      render();
      setMessage(errorEl, '');
      setMessage(successEl, '');
      try {
        const result = await request('/api/config/routing', {
          method: 'PUT',
          headers: { ...headers(true), 'If-Match': etag },
          body: JSON.stringify({ tiers: draft }),
        });
        if (loadGeneration !== generation) return;
        renderDocument(result.payload, result.etag);
        setMessage(successEl, 'Routing models saved and active.');
      } catch (error) {
        if (loadGeneration !== generation) return;
        if (error.status === 412) {
          setMessage(errorEl, 'Configuration changed in another tab. Your selections are preserved; reload before saving.');
        } else {
          setMessage(errorEl, error.message);
        }
      } finally {
        if (loadGeneration !== generation) return;
        busy = false;
        render();
      }
    }

    async function reset() {
      if (busy || !routingLoaded) return;
      const loadGeneration = generation;
      busy = true;
      render();
      setMessage(errorEl, '');
      setMessage(successEl, '');
      try {
        const result = await request('/api/config/routing', {
          method: 'DELETE',
          headers: { ...headers(), 'If-Match': etag },
        });
        if (loadGeneration !== generation) return;
        renderDocument(result.payload, result.etag);
        setMessage(successEl, 'Runtime override removed. File defaults are active.');
      } catch (error) {
        if (loadGeneration !== generation) return;
        if (error.status === 412) {
          setMessage(errorEl, 'Configuration changed in another tab. Reload before resetting.');
        } else {
          setMessage(errorEl, error.message);
        }
      } finally {
        if (loadGeneration !== generation) return;
        busy = false;
        render();
      }
    }

    function providerEditable(source) {
      return {
        enabled: source.enabled !== false,
        rejectEndpoints: [...new Set(source.rejectEndpoints || [])].sort(),
        rejectModels: [...new Set(source.rejectModels || [])].sort(),
      };
    }

    function providerSnapshot(providers) {
      return Object.fromEntries(Object.keys(providers || {}).sort().map(id => [id, providerEditable(providers[id])]));
    }

    function updateProviderDirty() {
      providerDirty = JSON.stringify(providerSnapshot(providerDraft)) !== JSON.stringify(providerSnapshot(providerState?.providers || {}));
      providerDirtyEl.textContent = providerDirty ? 'Unsaved changes' : '';
      providerDirtyEl.className = providerDirty ? 'dirty' : 'muted';
      providerSave.disabled = providerBusy || !providersLoaded || !providerDirty;
    }

    function validProviderModel(value, provider) {
      if (typeof value !== 'string' || value.length === 0 || value.length > 272
        || !/^[A-Za-z0-9._:/-]+$/.test(value) || value.startsWith('/') || value.endsWith('/')
        || value.includes('//') || value.split('/').some(part => part === '.' || part === '..')) return false;
      const owner = value.split('/', 1)[0];
      return !Object.hasOwn(providerState.providers, owner) || owner === provider;
    }

    function renderProviders() {
      providersEl.replaceChildren();
      const entries = Object.entries(providerState?.providers || {}).sort(([a], [b]) => a.localeCompare(b));
      entries.forEach(([id, metadata], index) => {
        const editable = providerDraft[id];
        const card = document.createElement('section');
        card.className = 'provider-card';
        const header = document.createElement('div');
        header.className = 'provider-head';
        const identity = document.createElement('div');
        const heading = document.createElement('h3');
        heading.textContent = id;
        const credential = addText(identity, 'muted', `Credential: ${String(metadata.credentialStatus || 'unknown').replaceAll('_', ' ')}`);
        credential.setAttribute('aria-label', `${id} credential status: ${String(metadata.credentialStatus || 'unknown').replaceAll('_', ' ')}`);
        identity.prepend(heading);
        const enabledLabel = document.createElement('label');
        enabledLabel.className = 'provider-toggle';
        const enabled = document.createElement('input');
        enabled.type = 'checkbox';
        enabled.checked = editable.enabled;
        enabled.disabled = providerBusy || !providersLoaded;
        enabled.addEventListener('change', () => { editable.enabled = enabled.checked; setMessage(providerSuccess, ''); updateProviderDirty(); });
        enabledLabel.append(enabled, document.createTextNode('Provider enabled'));
        header.append(identity, enabledLabel);
        card.appendChild(header);

        const endpointGroup = document.createElement('fieldset');
        endpointGroup.className = 'provider-group';
        const endpointLegend = document.createElement('legend');
        endpointLegend.textContent = 'Supported endpoints';
        endpointGroup.appendChild(endpointLegend);
        const endpointList = document.createElement('div');
        endpointList.className = 'provider-endpoints';
        const rejected = new Set(editable.rejectEndpoints);
        for (const endpoint of [...new Set(metadata.supportedEndpoints || [])].sort()) {
          const label = document.createElement('label');
          label.className = 'provider-endpoint';
          const checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.checked = !rejected.has(endpoint);
          checkbox.disabled = providerBusy || !providersLoaded;
          checkbox.addEventListener('change', () => {
            const next = new Set(editable.rejectEndpoints);
            if (checkbox.checked) next.delete(endpoint); else next.add(endpoint);
            editable.rejectEndpoints = [...next].sort();
            setMessage(providerSuccess, '');
            updateProviderDirty();
          });
          label.append(checkbox, document.createTextNode(` ${endpoint}`));
          endpointList.appendChild(label);
        }
        endpointGroup.appendChild(endpointList);
        card.appendChild(endpointGroup);

        const modelGroup = document.createElement('fieldset');
        modelGroup.className = 'provider-group';
        const modelLegend = document.createElement('legend');
        modelLegend.textContent = 'Rejected model IDs';
        modelGroup.appendChild(modelLegend);
        const modelList = document.createElement('ul');
        modelList.className = 'provider-model-list';
        editable.rejectModels.forEach((model, modelIndex) => {
          const item = document.createElement('li');
          const value = document.createElement('code');
          value.textContent = model;
          const remove = document.createElement('button');
          remove.type = 'button';
          remove.textContent = 'Remove';
          remove.setAttribute('aria-label', `Remove rejected model ${model}`);
          remove.disabled = providerBusy || !providersLoaded;
          remove.addEventListener('click', () => {
            editable.rejectModels.splice(modelIndex, 1);
            setMessage(providerSuccess, '');
            renderProviders();
            providersEl.querySelector(`#provider-model-${index}`).focus();
          });
          item.append(value, remove);
          modelList.appendChild(item);
        });
        modelGroup.appendChild(modelList);
        const addRow = document.createElement('div');
        addRow.className = 'provider-model-add';
        const input = document.createElement('input');
        input.id = `provider-model-${index}`;
        input.type = 'text';
        input.autocomplete = 'off';
        input.spellcheck = false;
        input.maxLength = 272;
        input.placeholder = 'Exact model ID';
        input.value = providerModelInputDrafts[id] || '';
        input.addEventListener('input', () => { providerModelInputDrafts[id] = input.value; });
        input.setAttribute('aria-label', `Model ID to reject for ${id}`);
        input.setAttribute('list', `provider-models-${index}`);
        input.disabled = providerBusy || !providersLoaded;
        const datalist = document.createElement('datalist');
        datalist.id = `provider-models-${index}`;
        for (const model of [...new Set(metadata.models || [])].sort()) {
          const option = document.createElement('option');
          option.value = model;
          datalist.appendChild(option);
        }
        const add = document.createElement('button');
        add.type = 'button';
        add.textContent = 'Add model';
        add.disabled = providerBusy || !providersLoaded;
        add.addEventListener('click', () => {
          const model = input.value.trim();
          if (!validProviderModel(model, id)) {
            setMessage(providerError, `Enter a valid exact model ID for ${id}, using letters, numbers, dot, underscore, colon, slash, or hyphen.`);
            input.focus();
            return;
          }
          if (editable.rejectModels.includes(model)) {
            setMessage(providerError, 'That exact model ID is already rejected for this provider.');
            input.focus();
            return;
          }
          editable.rejectModels.push(model);
          editable.rejectModels.sort();
          providerModelInputDrafts[id] = '';
          input.value = '';
          setMessage(providerError, '');
          setMessage(providerSuccess, '');
          updateProviderDirty();
          renderProviders();
          providersEl.querySelector(`#provider-model-${index}`).focus();
        });
        addRow.append(input, add, datalist);
        modelGroup.appendChild(addRow);
        card.appendChild(modelGroup);
        providersEl.appendChild(card);
      });
      providersEl.setAttribute('aria-busy', String(providerBusy));
      updateProviderDirty();
    }

    function receiveProviders(result) {
      providerState = result.payload;
      providerEtag = result.etag;
      providerDraft = Object.fromEntries(Object.entries(providerState.providers || {}).map(([id, value]) => [id, providerEditable(value)]));
      providersLoaded = true;
      providerStatus.textContent = providerState.configured ? 'Source: managed' : 'Source: defaults';
      setMessage(providerError, '');
      renderProviders();
    }

    async function loadProviders(loadGeneration) {
      providersLoaded = false;
      providerBusy = true;
      providerStatus.textContent = 'Loading…';
      setMessage(providerSuccess, '');
      if (providerState) renderProviders();
      else updateProviderDirty();
      try {
        const result = await request('/api/config/providers', { headers: headers() });
        if (loadGeneration !== generation) return;
        receiveProviders(result);
      } catch (error) {
        if (loadGeneration !== generation) return;
        providerStatus.textContent = 'Reload required';
        setMessage(providerError, error.message);
      } finally {
        if (loadGeneration !== generation) return;
        providerBusy = false;
        if (providerState) renderProviders();
        else updateProviderDirty();
      }
    }

    async function saveProviders() {
      if (!providerDirty || providerBusy || !providersLoaded) return;
      const loadGeneration = generation;
      const submitted = Object.fromEntries(Object.entries(providerDraft).map(([id, value]) => [id, providerEditable(value)]));
      providerBusy = true;
      renderProviders();
      setMessage(providerError, '');
      setMessage(providerSuccess, '');
      try {
        const providers = Object.fromEntries(Object.entries(submitted).map(([id, value]) => [id, {
          enabled: value.enabled,
          rejectEndpoints: value.rejectEndpoints,
          rejectModels: value.rejectModels,
        }]));
        const result = await request('/api/config/providers', {
          method: 'PUT',
          headers: { ...headers(true), 'If-Match': providerEtag },
          body: JSON.stringify({ providers }),
        });
        if (loadGeneration !== generation) return;
        receiveProviders(result);
        setMessage(providerSuccess, 'Provider settings saved.');
      } catch (error) {
        if (loadGeneration !== generation) return;
        if (error.status === 412) {
          providersLoaded = false;
          setMessage(providerError, 'Configuration changed in another tab. Your edits are preserved; reload before saving.');
        } else setMessage(providerError, error.message);
      } finally {
        if (loadGeneration !== generation) return;
        providerBusy = false;
        renderProviders();
      }
    }

    function variantId(model, contextWindow) {
      const context = Number(contextWindow);
      if (!model || !Number.isSafeInteger(context) || context < 1) return 'Enter a model and whole token count';
      return `${model}-${context % 1000 === 0 ? `${context / 1000}k` : context}context`;
    }

    function variantPairs(rows) {
      return rows.map(row => ({ id: row.id, model: row.model, contextWindow: row.contextWindow }));
    }

    function validateVariants(rows, models, saved) {
      const seen = new Set();
      for (const [index, row] of rows.entries()) {
        const id = row.id;
        const context = Number(row.contextWindow);
        const preserved = saved.some(item => item.id === row.id && item.model === row.model && item.contextWindow === context);
        if (typeof id !== 'string' || !id || id.length > 272 || !/^[A-Za-z0-9._:/-]+$/.test(id)
          || id.startsWith('/') || id.endsWith('/') || id.includes('//') || id.split('/').some(part => part === '.' || part === '..')) {
          return `Variant ${index + 1}: enter a model ID of 1–272 ASCII letters, digits or . _ : / - without empty or dot path segments.`;
        }
        if (id.startsWith('custom-1/') || id.startsWith('spartan-route/') || ['auto', 'clawroute/auto'].includes(id)
          || /(?:^|[/.-])(?:image|realtime)(?:$|[/.-])/.test(id) || (!preserved && models.some(model => model.id === id))) {
          return `Variant ${index + 1}: this name is reserved or belongs to a base model.`;
        }
        if (seen.has(id)) return `Variant ${index + 1}: this model ID already exists.`;
        seen.add(id);
        if (!/^\d+$/.test(String(row.contextWindow)) || !Number.isSafeInteger(context) || context < 1 || context > 2000000) {
          return `Variant ${index + 1}: enter a whole token count from 1 to 2,000,000.`;
        }
        const model = models.find(model => model.id === row.model);
        if (!model?.eligible && !preserved) return `Variant ${index + 1}: select an eligible base model.`;
        if (!preserved && model?.eligible && context > Math.min(model.maxContext, 2000000)) {
          return `Variant ${index + 1}: context exceeds ${model.id}'s maximum of ${Math.min(model.maxContext, 2000000).toLocaleString()} tokens.`;
        }
      }
      return '';
    }

    function updateVariantControls() {
      variantDirty = JSON.stringify(variantPairs(variantDraft)) !== JSON.stringify(variantPairs(variantState?.variants || []));
      variantDirtyEl.textContent = variantDirty ? 'Unsaved changes' : '';
      variantDirtyEl.className = variantDirty ? 'dirty' : 'muted';
      variantSave.disabled = variantBusy || !variantsLoaded || (!variantDirty && variantState?.configured !== false);
      variantAdd.disabled = variantBusy || !variantsLoaded || !(variantState?.models || []).some(model => model.eligible);
      variantsEl.setAttribute('aria-busy', String(variantBusy));
      for (const control of variantsEl.querySelectorAll('input, select, button')) control.disabled = variantBusy || !variantsLoaded;
    }

    function renderVariants() {
      variantsEl.replaceChildren();
      const models = (variantState?.models || []).filter(model => model.eligible).sort((a, b) => a.id.localeCompare(b.id));
      if (variantState && !variantDraft.length) addText(variantsEl, 'muted', 'No context variants. Add a variant to publish another context window.');
      variantDraft.forEach((item, index) => {
        const row = document.createElement('div');
        row.className = 'variant-row';
        const nameLabel = document.createElement('label');
        nameLabel.className = 'variant-field';
        nameLabel.htmlFor = `variant-name-${index}`;
        const nameText = document.createElement('span');
        nameText.className = 'label';
        nameText.textContent = 'Name (model ID)';
        const nameInput = document.createElement('input');
        nameInput.id = nameLabel.htmlFor;
        nameInput.type = 'text';
        nameInput.maxLength = 272;
        nameInput.autocomplete = 'off';
        nameInput.spellcheck = false;
        nameInput.value = item.id;
        nameLabel.append(nameText, nameInput);
        const modelLabel = document.createElement('label');
        modelLabel.className = 'variant-field';
        modelLabel.htmlFor = `variant-model-${index}`;
        addText(modelLabel, 'label', 'Base model');
        const select = document.createElement('select');
        select.id = modelLabel.htmlFor;
        for (const model of models) {
          const option = document.createElement('option');
          option.value = model.id;
          option.textContent = model.id;
          select.appendChild(option);
        }
        if (!models.some(model => model.id === item.model)) {
          const option = document.createElement('option');
          option.value = item.model;
          option.textContent = `${item.model} · unavailable`;
          option.disabled = true;
          select.prepend(option);
        }
        select.value = item.model;
        modelLabel.appendChild(select);
        const contextLabel = document.createElement('label');
        contextLabel.className = 'variant-field';
        contextLabel.htmlFor = `variant-context-${index}`;
        addText(contextLabel, 'label', 'Context window (tokens)');
        const input = document.createElement('input');
        input.id = contextLabel.htmlFor;
        input.type = 'number';
        input.min = '1';
        input.step = '1';
        input.inputMode = 'numeric';
        input.value = item.contextWindow;
        contextLabel.appendChild(input);
        const remove = document.createElement('button');
        remove.textContent = 'Remove';
        remove.setAttribute('aria-label', `Remove variant ${index + 1}`);
        const metadata = document.createElement('div');
        metadata.className = 'variant-meta tier-meta';
        const preview = addText(metadata, 'muted', '');
        const readiness = addText(metadata, 'muted', '');
        function refreshMetadata() {
          preview.textContent = `Model ID: ${item.id || 'Enter a name'}`;
          const model = variantState.models.find(model => model.id === item.model);
          input.max = String(Math.min(model?.maxContext || 2000000, 2000000));
          const saved = variantState.variants.find(saved => saved.id === item.id && saved.model === item.model && saved.contextWindow === Number(item.contextWindow));
          readiness.textContent = saved?.available === false
            ? `Unavailable: ${saved.unavailableReason || 'base model is unavailable'}. Preserve this row or remove it.`
            : `Maximum: ${Number(input.max).toLocaleString()} tokens · Readiness: ${(model?.readiness || 'unknown').replaceAll('_', ' ')}`;
        }
        function changed() {
          setMessage(variantSuccess, '');
          setMessage(variantError, '');
          refreshMetadata();
          updateVariantControls();
        }
        nameInput.oninput = () => { item.id = nameInput.value; changed(); };
        select.onchange = () => { item.model = select.value; changed(); };
        input.oninput = () => { item.contextWindow = input.value; changed(); };
        input.onchange = () => {
          if (/^\d+$/.test(input.value)) item.contextWindow = Number(input.value);
          changed();
        };
        remove.onclick = () => {
          variantDraft.splice(index, 1);
          setMessage(variantSuccess, '');
          setMessage(variantError, '');
          renderVariants();
          const next = variantsEl.querySelector('select');
          (next || variantAdd).focus();
        };
        refreshMetadata();
        row.append(nameLabel, modelLabel, contextLabel, remove, metadata);
        variantsEl.appendChild(row);
      });
      updateVariantControls();
    }

    function receiveVariants(result) {
      variantState = result.payload;
      variantEtag = result.etag;
      variantDraft = variantPairs(variantState.variants);
      variantsLoaded = true;
      variantStatus.textContent = variantState.configured ? 'Source: managed' : 'Source: default 282k';
      setMessage(variantWarning, variantState.warning);
      setMessage(variantError, '');
      renderVariants();
    }

    async function loadVariants(loadGeneration) {
      variantsLoaded = false;
      variantBusy = true;
      variantStatus.textContent = 'Loading…';
      setMessage(variantSuccess, '');
      setMessage(variantError, '');
      updateVariantControls();
      try {
        const result = await request('/api/config/model-variants', { headers: headers() });
        if (loadGeneration !== generation) return;
        receiveVariants(result);
      } catch (error) {
        if (loadGeneration !== generation) return;
        variantStatus.textContent = 'Reload required';
        setMessage(variantError, error.message);
      } finally {
        if (loadGeneration !== generation) return;
        variantBusy = false;
        updateVariantControls();
      }
    }

    async function saveVariants() {
      if ((!variantDirty && variantState?.configured !== false) || variantBusy || !variantsLoaded) return;
      setMessage(variantSuccess, '');
      const errorMessage = validateVariants(variantDraft, variantState.models, variantState.variants);
      if (errorMessage) { setMessage(variantError, errorMessage); return; }
      const loadGeneration = generation;
      variantBusy = true;
      updateVariantControls();
      setMessage(variantError, '');
      try {
        const result = await request('/api/config/model-variants', {
          method: 'PUT',
          headers: { ...headers(true), 'If-Match': variantEtag },
          body: JSON.stringify({ variants: variantDraft.map(row => ({ id: row.id, model: row.model, contextWindow: Number(row.contextWindow) })) }),
        });
        if (loadGeneration !== generation) return;
        receiveVariants(result);
        setMessage(variantSuccess, 'Context variants saved. Refresh your client’s model list.');
      } catch (error) {
        if (loadGeneration !== generation) return;
        if (error.status === 412) {
          variantsLoaded = false;
          setMessage(variantError, 'Configuration changed in another tab. Your edits are preserved; reload before saving.');
        } else setMessage(variantError, error.message);
      } finally {
        if (loadGeneration !== generation) return;
        variantBusy = false;
        updateVariantControls();
      }
    }

    function invalidateResources() {
      generation += 1;
      routingLoaded = false;
      variantsLoaded = false;
      providersLoaded = false;
      busy = false;
      variantBusy = false;
      providerBusy = false;
      setMessage(successEl, '');
      setMessage(variantSuccess, '');
      setMessage(providerSuccess, '');
      statusEl.textContent = 'Reload required';
      variantStatus.textContent = 'Reload required';
      if (documentState) render();
      updateVariantControls();
      if (providerState) renderProviders();
    }

    function load() {
      invalidateResources();
      return Promise.all([loadRouting(generation), loadVariants(generation), loadProviders(generation)]);
    }

    tokenInput.addEventListener('input', invalidateResources);
    variantSave.onclick = saveVariants;
    providerSave.onclick = saveProviders;
    variantAdd.onclick = () => {
      if (variantAdd.disabled) return;
      const model = variantState.models.find(model => model.eligible);
      const contextWindow = Math.min(model.maxContext, 282000, 2000000);
      variantDraft.push({ id: variantId(model.id, contextWindow), model: model.id, contextWindow });
      setMessage(variantSuccess, '');
      setMessage(variantError, '');
      renderVariants();
      document.getElementById(`variant-name-${variantDraft.length - 1}`).focus();
    };

    document.getElementById('use-token').onclick = () => {
      sessionStorage.setItem('spartanRouteToken', tokenInput.value.trim());
      load();
    };
    document.getElementById('reload').onclick = () => load();
    saveButton.onclick = () => save();
    resetButton.onclick = () => reset();
    window.addEventListener('beforeunload', event => {
      if (!dirty && !variantDirty && !providerDirty) return;
      event.preventDefault();
      event.returnValue = '';
    });
    load();
