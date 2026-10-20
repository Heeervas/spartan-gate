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
      const response = await fetch(path, options);
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
      busy = false;
      variantBusy = false;
      setMessage(successEl, '');
      setMessage(variantSuccess, '');
      statusEl.textContent = 'Reload required';
      variantStatus.textContent = 'Reload required';
      if (documentState) render();
      updateVariantControls();
    }

    function load() {
      invalidateResources();
      return Promise.all([loadRouting(generation), loadVariants(generation)]);
    }

    tokenInput.addEventListener('input', invalidateResources);
    variantSave.onclick = saveVariants;
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
      if (!dirty && !variantDirty) return;
      event.preventDefault();
      event.returnValue = '';
    });
    load();
