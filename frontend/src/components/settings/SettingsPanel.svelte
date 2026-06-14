<script lang="ts">
  /**
   * SettingsPanel — application settings page.
   *
   * Exposes the active embedding model selector.  Four provider types are
   * supported, each requiring different extra fields:
   *
   * - **Voyage AI** (voyage-3, voyage-3-large, etc.) — no extra fields; API
   *   key is supplied via the ``VOYAGE_API_KEY`` environment variable.
   * - **OpenAI** (text-embedding-3-small) — an OpenAI API key field appears;
   *   the key is stored server-side and only a presence flag is returned.
   * - **RexBERT** (rexbert-micro/mini/base/large) — a shared endpoint URL and
   *   optional bearer key appear; all variants use the same endpoint, with the
   *   model id sent as the ``model`` field in each request.
   * - **Custom** — endpoint URL, optional bearer key, model name, and vector
   *   dimension fields all appear.
   *
   * Selecting a different model and clicking "Save" calls PUT /v1/settings and
   * applies the change immediately at runtime without a container restart.
   *
   * When the new model produces vectors of a different dimension than the
   * previous one a prominent warning explains that all existing indexes must be
   * fully rebuilt before semantic search will work again.
   */
  import { onMount } from 'svelte';
  import { settingsStore } from '../../lib/store';
  import { settingsService } from '../../lib/services/settings/SettingsService';
  import type { AppSettings } from '../../lib/types';
  import { modelOption, effectiveDim, dimensionWillChange } from './SettingsPanel.helpers';

  $: settings = $settingsStore.settings;
  $: loading = $settingsStore.loading;

  let selectedModel = '';
  let saving = false;
  let errMsg = '';
  let saved = false;
  let dimWarning = '';

  // ── RexBERT standalone config ────────────────────────────────────────────────

  /** Whether the "RexBERT server config" section is currently saving. */
  let rexbertSaving = false;
  let rexbertSaved = false;
  let rexbertErr = '';

  // ── Extra provider fields ────────────────────────────────────────────────────

  /** OpenAI API key input value (only sent when the user fills it in). */
  let openaiApiKey = '';

  /** RexBERT shared endpoint URL (all rexbert-* variants use this). */
  let rexbertEndpoint = '';

  /** RexBERT bearer token (optional). */
  let rexbertApiKey = '';

  /** Custom endpoint URL input. */
  let customEndpoint = '';

  /** Custom bearer token (optional). */
  let customApiKey = '';

  /** Custom model name sent in the request body. */
  let customModelName = '';

  /** Custom vector dimension. */
  let customNumDim = 1536;

  async function load() {
    await settingsStore.load();
    if (settings) {
      selectedModel   = settings.embed_model;
      rexbertEndpoint = settings.rexbert_endpoint  ?? '';
      customEndpoint  = settings.custom_endpoint   ?? '';
      customModelName = settings.custom_model_name ?? '';
      customNumDim    = settings.custom_num_dim     ?? 1536;
    }
  }

  onMount(load);

  $: selectedProvider = settings ? (modelOption(settings, selectedModel)?.provider ?? 'voyage') : 'voyage';

  $: isDirty = settings !== null && (
    selectedModel !== settings.embed_model ||
    (selectedProvider === 'openai'   && openaiApiKey.trim() !== '') ||
    (selectedProvider === 'rexbert'  && (
      rexbertEndpoint !== (settings.rexbert_endpoint ?? '') ||
      rexbertApiKey.trim() !== ''
    )) ||
    (selectedProvider === 'custom'   && (
      customEndpoint  !== (settings.custom_endpoint   ?? '') ||
      customModelName !== (settings.custom_model_name ?? '') ||
      customNumDim    !== (settings.custom_num_dim    ?? 0)
    ))
  );

  async function save() {
    if (!selectedModel) return;
    saving = true; errMsg = ''; dimWarning = ''; saved = false;
    try {
      const result = await settingsStore.save(selectedModel, {
        openaiApiKey:    selectedProvider === 'openai'   && openaiApiKey.trim()   ? openaiApiKey.trim()   : undefined,
        rexbertEndpoint: selectedProvider === 'rexbert'  && rexbertEndpoint.trim() ? rexbertEndpoint.trim() : undefined,
        rexbertApiKey:   selectedProvider === 'rexbert'  && rexbertApiKey.trim()   ? rexbertApiKey.trim()   : undefined,
        customEndpoint:  selectedProvider === 'custom' ? customEndpoint  : undefined,
        customApiKey:    selectedProvider === 'custom' ? customApiKey    : undefined,
        customModelName: selectedProvider === 'custom' ? customModelName : undefined,
        customNumDim:    selectedProvider === 'custom' ? customNumDim    : undefined,
      });
      selectedModel   = result.embed_model;
      rexbertEndpoint = result.rexbert_endpoint  ?? '';
      customEndpoint  = result.custom_endpoint   ?? '';
      customModelName = result.custom_model_name ?? '';
      customNumDim    = result.custom_num_dim    ?? 1536;
      openaiApiKey = ''; rexbertApiKey = '';
      saved = true;
      if (result.dim_changed) {
        dimWarning =
          `Vector dimension changed to ${result.embed_model_num_dim}-d. ` +
          'All existing indexes must be fully rebuilt before semantic search will work correctly.';
      }
    } catch (e) {
      errMsg = String(e);
    } finally {
      saving = false;
    }
  }

  /** Clear saved/warning state whenever the user changes any field. */
  function onAnyChange() {
    saved = false;
    dimWarning = '';
  }

  /**
   * Save the RexBERT endpoint and optional API key without changing the global model.
   * Used when the global is voyage/openai but per-index RexBERT overrides are needed.
   */
  async function saveRexbertOnly() {
    if (!rexbertEndpoint.trim()) return;
    rexbertSaving = true; rexbertErr = ''; rexbertSaved = false;
    try {
      const result = await settingsService.saveRexbertConfig(
        rexbertEndpoint.trim(),
        rexbertApiKey.trim() || undefined,
      );
      // Reflect the now-stored endpoint back so the "(stored)" label appears
      rexbertEndpoint = result.rexbert_endpoint ?? rexbertEndpoint;
      rexbertApiKey = '';
      rexbertSaved = true;
      // Refresh the store so rexbert_endpoint shows correctly everywhere
      await settingsStore.load();
    } catch (e) {
      rexbertErr = String(e);
    } finally {
      rexbertSaving = false;
    }
  }
</script>

<div class="panel">
  <div class="panel-head">
    <div>
      <h2>Settings</h2>
      <p class="panel-sub">Application-wide configuration.</p>
    </div>
  </div>

  {#if errMsg}
    <p class="err">{errMsg}</p>
  {/if}

  {#if loading}
    <div class="loading"><div class="spinner"></div><span>Loading…</span></div>
  {:else if settings}

    <!-- Embedding Model Section -->
    <section class="section">
      <div class="section-header">
        <h3>Embedding Model</h3>
        <p class="section-sub">
          Model used to generate vectors during ingest and rebuild.
          Applied immediately — no restart required.
        </p>
      </div>

      <div class="field-group">
        <label class="field-label" for="model-select">Active model</label>

        <div class="select-row">
          <select
            id="model-select"
            class="model-select"
            bind:value={selectedModel}
            on:change={onAnyChange}
            disabled={saving}
          >
            {#each settings.available_models as m}
              <option value={m.id}>{m.label}{m.num_dim > 0 ? ` — ${m.num_dim}d` : ''}</option>
            {/each}
          </select>

          <button
            class="btn-save"
            on:click={save}
            disabled={saving || !isDirty}
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>

        <!-- Selected model description card -->
        {#if settings && modelOption(settings, selectedModel)}
          {@const opt = modelOption(settings, selectedModel)}
          <div class="model-card" class:model-card-dirty={isDirty}>
            <div class="model-card-top">
              <span class="model-name">{opt.label}</span>
              {#if opt.num_dim > 0}
                <span class="dim-badge">{opt.num_dim}-d</span>
              {/if}
              <span class="provider-badge provider-{opt.provider}">{opt.provider}</span>
              {#if settings.embed_model === opt.id}
                <span class="active-badge">active</span>
              {/if}
            </div>
            <p class="model-desc">{opt.description}</p>
          </div>
        {/if}

        <!-- ── RexBERT provider extra fields ─────────────────────────────── -->
        {#if selectedProvider === 'rexbert'}
          <div class="provider-fields">
            <div class="field-row">
              <label class="field-label" for="rexbert-endpoint">
                Endpoint URL
                {#if settings.rexbert_endpoint}
                  <span class="key-stored">(stored — update to change)</span>
                {/if}
              </label>
              <input
                id="rexbert-endpoint"
                class="text-input"
                type="url"
                placeholder="https://rexbert.internal/v1/embeddings"
                bind:value={rexbertEndpoint}
                on:input={onAnyChange}
                disabled={saving}
              />
            </div>
            <div class="field-row">
              <label class="field-label" for="rexbert-key">
                API key (optional)
                {#if settings.rexbert_api_key_set}
                  <span class="key-stored">(key stored — leave blank to keep)</span>
                {/if}
              </label>
              <input
                id="rexbert-key"
                class="text-input"
                type="password"
                placeholder={settings.rexbert_api_key_set ? '••••••••••••' : 'Bearer token if required'}
                bind:value={rexbertApiKey}
                on:input={onAnyChange}
                disabled={saving}
              />
            </div>
          </div>
        {/if}

        <!-- ── OpenAI provider extra fields ──────────────────────────────── -->
        {#if selectedProvider === 'openai'}
          <div class="provider-fields">
            <div class="field-row">
              <label class="field-label" for="openai-key">
                OpenAI API key
                {#if settings.openai_api_key_set}
                  <span class="key-stored">(key stored — leave blank to keep)</span>
                {/if}
              </label>
              <input
                id="openai-key"
                class="text-input"
                type="password"
                placeholder={settings.openai_api_key_set ? '••••••••••••' : 'sk-…'}
                bind:value={openaiApiKey}
                on:input={onAnyChange}
                disabled={saving}
              />
            </div>
          </div>
        {/if}

        <!-- ── Custom provider extra fields ──────────────────────────────── -->
        {#if selectedProvider === 'custom'}
          <div class="provider-fields">
            <div class="field-row">
              <label class="field-label" for="custom-endpoint">Endpoint URL <span class="required">*</span></label>
              <input
                id="custom-endpoint"
                class="text-input"
                type="url"
                placeholder="https://my-server/v1/embeddings"
                bind:value={customEndpoint}
                on:input={onAnyChange}
                disabled={saving}
              />
            </div>
            <div class="field-row">
              <label class="field-label" for="custom-apikey">API key (optional)</label>
              <input
                id="custom-apikey"
                class="text-input"
                type="password"
                placeholder="Bearer token if required"
                bind:value={customApiKey}
                on:input={onAnyChange}
                disabled={saving}
              />
            </div>
            <div class="field-row-split">
              <div class="field-row">
                <label class="field-label" for="custom-model">Model name <span class="required">*</span></label>
                <input
                  id="custom-model"
                  class="text-input"
                  type="text"
                  placeholder="my-embed-model"
                  bind:value={customModelName}
                  on:input={onAnyChange}
                  disabled={saving}
                />
              </div>
              <div class="field-row">
                <label class="field-label" for="custom-dim">Dimensions <span class="required">*</span></label>
                <input
                  id="custom-dim"
                  class="text-input dim-input"
                  type="number"
                  min="1"
                  placeholder="1536"
                  bind:value={customNumDim}
                  on:input={onAnyChange}
                  disabled={saving}
                />
              </div>
            </div>
          </div>
        {/if}

        <!-- Pre-save dimension change warning -->
        {#if settings && isDirty && dimensionWillChange(settings, selectedModel, customNumDim)}
          <div class="warn-box">
            <span class="warn-icon">⚠</span>
            <div>
              <strong>Dimension change</strong> — the new model produces
              {effectiveDim(settings, selectedModel, customNumDim)}-d vectors vs the current
              {settings.embed_model === 'custom'
                ? (settings.custom_num_dim ?? 0)
                : (modelOption(settings, settings.embed_model)?.num_dim ?? 0)}d.
              All existing indexes must be <strong>fully rebuilt</strong> before
              semantic search will work correctly.
            </div>
          </div>
        {/if}

        <!-- Post-save feedback -->
        {#if saved && !dimWarning}
          <p class="success">Model updated successfully.</p>
        {/if}

        {#if dimWarning}
          <div class="warn-box warn-box-post">
            <span class="warn-icon">⚠</span>
            <div>{dimWarning}</div>
          </div>
        {/if}
      </div>

      <!-- Model comparison table -->
      <div class="model-table-wrap">
        <div class="model-table-label">All available models</div>
        <table class="model-table">
          <thead>
            <tr>
              <th>Model</th>
              <th>Provider</th>
              <th>Dimensions</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {#each settings.available_models as m}
              <tr
                class:row-active={m.id === settings.embed_model}
                class:row-selected={m.id === selectedModel && m.id !== settings.embed_model}
              >
                <td class="td-model">
                  {m.label}
                  {#if m.id === settings.embed_model}
                    <span class="active-badge">active</span>
                  {/if}
                </td>
                <td class="td-provider">
                  <span class="provider-badge provider-{m.provider}">{m.provider}</span>
                </td>
                <td class="td-dim">{m.num_dim > 0 ? m.num_dim : '—'}</td>
                <td class="td-desc">{m.description}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </section>

    <!-- RexBERT Server Config — always visible so the endpoint can be stored
         independently of the global model (needed for per-index RexBERT overrides). -->
    <section class="section rexbert-section">
      <div class="section-header">
        <h3>RexBERT Server</h3>
        <p class="section-sub">
          Configure the RexBERT inference endpoint. This is stored independently
          of the global model so per-index RexBERT overrides work even when the
          global model is Voyage or OpenAI.
        </p>
      </div>

      <div class="field-group">
        <div class="field-row">
          <label class="field-label" for="rexbert-standalone-endpoint">
            Endpoint URL
            {#if settings.rexbert_endpoint}
              <span class="key-stored">(stored — update to change)</span>
            {/if}
          </label>
          <input
            id="rexbert-standalone-endpoint"
            class="text-input"
            type="url"
            placeholder="http://rexbert.internal/v1/embeddings"
            bind:value={rexbertEndpoint}
            disabled={rexbertSaving}
          />
        </div>
        <div class="field-row">
          <label class="field-label" for="rexbert-standalone-key">
            API key (optional)
            {#if settings.rexbert_api_key_set}
              <span class="key-stored">(key stored — leave blank to keep)</span>
            {/if}
          </label>
          <input
            id="rexbert-standalone-key"
            class="text-input"
            type="password"
            placeholder={settings.rexbert_api_key_set ? '••••••••••••' : 'Bearer token if required'}
            bind:value={rexbertApiKey}
            disabled={rexbertSaving}
          />
        </div>

        {#if rexbertErr}<p class="err">{rexbertErr}</p>{/if}
        {#if rexbertSaved}<p class="success">RexBERT endpoint saved.</p>{/if}

        <button
          class="btn-save"
          on:click={saveRexbertOnly}
          disabled={rexbertSaving || !rexbertEndpoint.trim()}
        >
          {rexbertSaving ? 'Saving…' : 'Save RexBERT config'}
        </button>
      </div>
    </section>

  {/if}
</div>

<style>
  .panel {
    padding: 32px 36px 48px;
    max-width: 820px;
  }

  .panel-head { margin-bottom: 28px; }
  h2 { margin: 0; font-size: 20px; font-weight: 600; color: #eee; }
  .panel-sub { margin: 4px 0 0; font-size: 13px; color: #555; }

  .loading { display: flex; align-items: center; gap: 10px; color: #555; font-size: 13px; }
  .spinner {
    width: 18px; height: 18px;
    border: 2px solid #222; border-top-color: #7c6cf4;
    border-radius: 50%; animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .err { color: #e55; font-size: 13px; margin: 0 0 16px; }
  .success { color: #5cdb85; font-size: 13px; margin: 8px 0 0; }

  /* Section */
  .section { display: flex; flex-direction: column; gap: 20px; }
  .rexbert-section { margin-top: 32px; padding-top: 28px; border-top: 1px solid #222; }
  .section-header { display: flex; flex-direction: column; gap: 4px; }
  h3 { margin: 0; font-size: 15px; font-weight: 600; color: #ddd; }
  .section-sub { margin: 0; font-size: 12px; color: #555; }

  /* Field group */
  .field-group { display: flex; flex-direction: column; gap: 10px; }
  .field-label { font-size: 12px; color: #888; font-weight: 500; }

  .select-row { display: flex; gap: 10px; align-items: center; }

  .model-select {
    flex: 1;
    background: #151515;
    border: 1px solid #2e2e2e;
    border-radius: 7px;
    padding: 9px 12px;
    color: #eee;
    font-size: 13px;
    font-family: inherit;
    cursor: pointer;
    appearance: auto;
  }
  .model-select:focus { outline: none; border-color: #7c6cf4; }
  .model-select:disabled { opacity: 0.5; cursor: not-allowed; }

  .btn-save {
    background: #7c6cf4;
    color: #fff;
    border: none;
    border-radius: 7px;
    padding: 9px 22px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .btn-save:hover:not(:disabled) { background: #6b5ce7; }
  .btn-save:disabled { opacity: 0.4; cursor: not-allowed; }

  /* Model card */
  .model-card {
    background: #0f0f0f;
    border: 1px solid #1e1e1e;
    border-radius: 8px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 5px;
    transition: border-color 0.15s;
  }
  .model-card-dirty { border-color: #3a3070; }

  .model-card-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

  .model-name {
    font-size: 13px;
    font-weight: 600;
    color: #ddd;
    font-family: 'SF Mono', 'Fira Code', monospace;
  }

  .dim-badge {
    font-size: 10px;
    font-weight: 700;
    background: #1e1b3a;
    color: #9d91f5;
    border: 1px solid #2e2b55;
    border-radius: 4px;
    padding: 1px 6px;
  }

  .active-badge {
    font-size: 10px;
    font-weight: 700;
    background: #0f2a1a;
    color: #5cdb85;
    border: 1px solid #1e3d29;
    border-radius: 4px;
    padding: 1px 6px;
  }

  /* Provider badge — colour-coded per provider */
  .provider-badge {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    border-radius: 4px;
    padding: 1px 6px;
  }
  .provider-voyage  { background: #1a1636; color: #9d91f5; border: 1px solid #2e2b55; }
  .provider-openai  { background: #0d1f14; color: #5cdb85; border: 1px solid #1a3322; }
  .provider-rexbert { background: #1a0d1a; color: #d070d0; border: 1px solid #4a1a4a; }
  .provider-custom  { background: #1a1200; color: #c8a040; border: 1px solid #4a3600; }

  .model-desc { margin: 0; font-size: 12px; color: #555; }

  /* ── Provider-specific extra fields ─────────────────────────────────────── */
  .provider-fields {
    background: #0d0d0d;
    border: 1px solid #1e1e1e;
    border-radius: 8px;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .field-row {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  .field-row-split {
    display: grid;
    grid-template-columns: 1fr 140px;
    gap: 12px;
  }

  .text-input {
    background: #151515;
    border: 1px solid #2e2e2e;
    border-radius: 6px;
    padding: 8px 11px;
    color: #eee;
    font-size: 13px;
    font-family: inherit;
    width: 100%;
    box-sizing: border-box;
  }
  .text-input:focus { outline: none; border-color: #7c6cf4; }
  .text-input:disabled { opacity: 0.5; cursor: not-allowed; }
  .text-input::placeholder { color: #444; }

  .dim-input { font-variant-numeric: tabular-nums; }

  .key-stored { font-size: 11px; color: #555; font-weight: 400; margin-left: 4px; }
  .required   { color: #c55; font-size: 11px; }

  /* Warning box */
  .warn-box {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    background: #1a1200;
    border: 1px solid #4a3600;
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 12px;
    color: #c8a040;
    line-height: 1.55;
  }
  .warn-box-post { background: #180f00; border-color: #5a2800; color: #d06030; }
  .warn-icon { font-size: 14px; flex-shrink: 0; padding-top: 1px; }
  .warn-box strong { color: #f0c060; }
  .warn-box-post strong { color: #f08040; }

  /* Model comparison table */
  .model-table-wrap { display: flex; flex-direction: column; gap: 8px; }

  .model-table-label {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: #444;
  }

  .model-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }

  .model-table th {
    text-align: left;
    color: #555;
    font-weight: 500;
    padding: 7px 12px;
    border-bottom: 1px solid #1e1e1e;
  }

  .model-table td {
    padding: 10px 12px;
    border-bottom: 1px solid #141414;
    color: #888;
    vertical-align: middle;
  }

  .model-table tr:last-child td { border-bottom: none; }

  .model-table .row-active td { background: #0f1a10; color: #aaa; }
  .model-table .row-active .td-model { color: #ddd; font-weight: 600; }

  .model-table .row-selected td { background: #0f0f1a; color: #aaa; }
  .model-table .row-selected .td-model { color: #9d91f5; font-weight: 600; }

  .td-model {
    font-family: 'SF Mono', 'Fira Code', monospace;
    white-space: nowrap;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .td-provider { white-space: nowrap; }

  .td-dim {
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
    color: #6b5ce7;
    font-weight: 600;
  }
</style>
