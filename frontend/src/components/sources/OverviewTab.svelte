<script lang="ts">
  import { indexService } from '../../lib/services/indexes/IndexService';
  import type { Index, IngestStatus } from '../../lib/services/indexes/index.model';
  import type { EmbedModelOption } from '../../lib/types';

  export let indexId: string;
  export let meta: Index;
  export let status: IngestStatus | null;
  export let availableModels: EmbedModelOption[];

  // ── Embedding model ───────────────────────────────────────────────────────────

  let selectedEmbedModel = meta.embedModel ?? '';
  let editingModel       = false;
  let modelSaving        = false;
  let modelSaved         = false;
  let modelErr           = '';

  $: currentDim   = availableModels.find(m => m.id === (meta?.embedModel || ''))?.num_dim ?? 0;
  $: selectedDim  = availableModels.find(m => m.id === selectedEmbedModel)?.num_dim ?? 0;
  $: dimWillChange = selectedDim > 0 && currentDim > 0 && selectedDim !== currentDim;
  $: isModelDirty  = selectedEmbedModel !== (meta.embedModel ?? '');

  async function saveEmbedModel() {
    if (!isModelDirty) return;
    modelSaving = true; modelErr = ''; modelSaved = false;
    try {
      meta = await indexService.setIndexEmbedModel(indexId, selectedEmbedModel);
      selectedEmbedModel = meta.embedModel ?? '';
      modelSaved = true;
      editingModel = false;
    } catch (e) {
      modelErr = String(e);
    } finally {
      modelSaving = false;
    }
  }

  function cancelEditModel() {
    selectedEmbedModel = meta.embedModel ?? '';
    editingModel = false;
    modelErr = '';
    modelSaved = false;
  }

  // ── Version boost ─────────────────────────────────────────────────────────────

  let versionBoostSaving = false;
  let versionBoostErr    = '';

  async function toggleVersionBoost() {
    versionBoostSaving = true; versionBoostErr = '';
    try {
      meta = await indexService.setVersionBoost(indexId, !meta.versionBoost);
    } catch (e) {
      versionBoostErr = String(e);
    } finally {
      versionBoostSaving = false;
    }
  }
</script>

<div class="overview">

  <!-- Last ingest status -->
  {#if status && status.state !== 'idle'}
    <div class="status-card" class:running={status.state === 'running'}
         class:error={status.state === 'error'}>
      <div class="status-row">
        <span class="status-label">Last ingest</span>
        <span class="status-badge">{status.state}</span>
      </div>
      {#if status.state === 'done' || status.state === 'running'}
        <p>{status.indexed} / {status.total || '?'} documents indexed</p>
      {/if}
      {#if status.error}
        <p class="err">{status.error}</p>
      {/if}
    </div>
  {/if}

  <!-- Connectors -->
  {#if meta.connectors.length > 0}
    <h2>Connectors</h2>
    {#each meta.connectors as conn}
      <div class="connector-card">
        <div class="conn-type">{conn.type}</div>
        <div class="conn-url">
          {#if conn.type === 'gsheet'}
            {String(conn.spreadsheet_id ?? '')} / {String(conn.sheet_name ?? '')}
          {:else}
            {String(conn.url ?? '')}
          {/if}
        </div>
      </div>
    {/each}
  {:else}
    <div class="empty-connectors">
      No connectors configured. Use the ingest endpoints to add data to this index.
    </div>
  {/if}

  <!-- Index settings -->
  <div class="settings-section">
    <h2>Index settings</h2>

    <!-- Embedding model card -->
    <div class="setting-card">
      <div class="setting-header">
        <div class="setting-title-group">
          <span class="setting-title">Embedding model</span>
          <span class="setting-hint">
            Determines how documents and queries are vectorised for semantic search.
            Changing the model requires a full rebuild.
          </span>
        </div>
        <div class="setting-current">
          {#if meta.embedModel}
            <span class="embed-model-badge embed-model-badge-custom">{meta.embedModel}</span>
          {:else}
            <span class="embed-model-badge embed-model-badge-default">global default</span>
          {/if}
          {#if !editingModel}
            <button class="btn-edit" on:click={() => { editingModel = true; modelSaved = false; }}>
              Change
            </button>
          {/if}
        </div>
      </div>

      {#if editingModel}
        <div class="model-editor">
          <div class="model-grid">
            <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
            <label class="model-card" class:model-card-selected={selectedEmbedModel === ''}>
              <input type="radio" name="idx-embed-model" class="model-radio"
                value="" bind:group={selectedEmbedModel} />
              <div class="model-card-info">
                <div class="model-card-top">
                  <span class="model-card-name">Global default</span>
                  <span class="provider-badge provider-voyage">voyage</span>
                </div>
                <span class="model-card-desc">
                  Uses whatever model is active in Settings. Recommended unless this
                  index needs a specific model.
                </span>
              </div>
            </label>

            {#each availableModels as m}
              <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
              <label class="model-card" class:model-card-selected={selectedEmbedModel === m.id}>
                <input type="radio" name="idx-embed-model" class="model-radio"
                  value={m.id} bind:group={selectedEmbedModel} />
                <div class="model-card-info">
                  <div class="model-card-top">
                    <span class="model-card-name">{m.label}</span>
                    {#if m.num_dim > 0}
                      <span class="dim-badge">{m.num_dim}-d</span>
                    {/if}
                    <span class="provider-badge provider-{m.provider}">{m.provider}</span>
                  </div>
                  <span class="model-card-desc">{m.description}</span>
                </div>
              </label>
            {/each}
          </div>

          {#if dimWillChange}
            <div class="dim-warn">
              <span class="warn-icon">⚠</span>
              <div>
                Dimension change: <strong>{currentDim}-d → {selectedDim}-d</strong>.
                After saving, <strong>rebuild this index</strong> before semantic search
                will work — existing vectors have the wrong dimension.
              </div>
            </div>
          {/if}

          {#if modelErr}<p class="err">{modelErr}</p>{/if}

          <div class="model-editor-actions">
            <button class="btn-cancel" on:click={cancelEditModel} disabled={modelSaving}>Cancel</button>
            <button class="btn-save" on:click={saveEmbedModel} disabled={modelSaving || !isModelDirty}>
              {modelSaving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      {/if}

      {#if modelSaved}
        <p class="model-saved">
          Embedding model updated.
          {#if dimWillChange}Rebuild the index to apply the new vectors.{/if}
        </p>
      {/if}
    </div>

    <!-- Version boost card -->
    <div class="setting-card">
      <div class="setting-header">
        <div class="setting-title-group">
          <span class="setting-title">Version boost</span>
          <span class="setting-hint">
            Favours newer product generations (e.g. iPhone 16 over iPhone 14) when
            relevance scores are close. Version number is extracted from the product
            title at index time — rebuild to apply to existing documents.
          </span>
        </div>
        <div class="setting-current">
          <label class="toggle-switch" aria-label="Toggle version boost">
            <input
              type="checkbox"
              checked={meta.versionBoost ?? false}
              disabled={versionBoostSaving}
              on:change={toggleVersionBoost}
            />
            <span class="toggle-slider"></span>
          </label>
          <span class="toggle-label">{meta.versionBoost ? 'On' : 'Off'}</span>
        </div>
      </div>
      {#if versionBoostErr}<p class="err">{versionBoostErr}</p>{/if}
    </div>
  </div>

</div>

<style>
  .overview { display: flex; flex-direction: column; gap: 20px; }
  h2 { font-size: 13px; color: var(--text-4); font-weight: 600; margin: 0;
       text-transform: uppercase; letter-spacing: 0.06em; }
  .err { color: var(--danger-text) !important; font-size: 12px; margin: 6px 0 0; }

  .status-card {
    background: #0f1a15; border: 1px solid #1e3028; border-radius: 8px; padding: 14px 16px;
  }
  .status-card.running { background: #0f1520; border-color: #1e2a40; }
  .status-card.error   { background: #1a0f0f; border-color: #3a1e1e; }
  .status-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
  .status-label { font-size: 12px; color: var(--text-4); }
  .status-badge {
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    padding: 2px 7px; border-radius: 4px; background: #1e3028; color: #5cdb85;
  }
  .status-card.running .status-badge { background: #1e2a40; color: #70a8ff; }
  .status-card.error   .status-badge { background: #3a1e1e; color: var(--danger-text); }
  .status-card p { margin: 0; font-size: 12px; color: var(--text-4); }

  .connector-card {
    background: var(--bg-3); border: 1px solid var(--border); border-radius: 8px;
    padding: 12px 14px; display: flex; gap: 12px; align-items: center;
  }
  .conn-type {
    background: var(--accent-dim); color: var(--accent); font-size: 11px; font-weight: 600;
    text-transform: uppercase; padding: 2px 7px; border-radius: 4px; flex-shrink: 0;
  }
  .conn-url { font-size: 12px; color: var(--text-4); word-break: break-all; }
  .empty-connectors { font-size: 13px; color: var(--text-5); text-align: center; padding: 32px 0; }

  .settings-section { display: flex; flex-direction: column; gap: 12px; }
  .setting-card {
    background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px;
    display: flex; flex-direction: column; gap: 12px;
  }
  .setting-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
  .setting-title-group { display: flex; flex-direction: column; gap: 3px; }
  .setting-title { font-size: 13px; font-weight: 600; color: var(--text-2); }
  .setting-hint { font-size: 12px; color: var(--text-5); max-width: 480px; line-height: 1.5; }
  .setting-current { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }

  .embed-model-badge {
    font-size: 11px; font-weight: 600; padding: 3px 9px; border-radius: 10px; letter-spacing: 0.02em;
  }
  .embed-model-badge-custom  { background: #1e2a40; color: #70a8ff; }
  .embed-model-badge-default { background: var(--surface); color: var(--text-4); }

  .btn-edit {
    background: none; border: 1px solid var(--border-2); border-radius: 6px;
    color: var(--text-4); font-size: 12px; padding: 4px 12px; cursor: pointer;
  }
  .btn-edit:hover { border-color: var(--text-5); color: var(--text-2); }

  .model-editor { display: flex; flex-direction: column; gap: 12px; }
  .model-grid   { display: flex; flex-direction: column; gap: 8px; }
  .model-card {
    display: flex; align-items: flex-start; gap: 12px;
    background: var(--bg-3); border: 1px solid var(--border-2); border-radius: 8px;
    padding: 12px 14px; cursor: pointer; transition: border-color 0.15s;
  }
  .model-card:hover { border-color: var(--text-5); }
  .model-card-selected { border-color: var(--accent); background: var(--accent-dim); }
  .model-radio { margin-top: 2px; accent-color: var(--accent); flex-shrink: 0; }
  .model-card-info { display: flex; flex-direction: column; gap: 4px; }
  .model-card-top  { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
  .model-card-name { font-size: 13px; font-weight: 600; color: var(--text-2); }
  .model-card-desc { font-size: 12px; color: var(--text-5); line-height: 1.4; }

  .dim-badge {
    font-size: 11px; font-weight: 600; padding: 1px 6px;
    background: #1e2a1e; color: #5cdb85; border-radius: 4px;
  }
  .provider-badge {
    font-size: 10px; font-weight: 700; padding: 1px 6px;
    border-radius: 4px; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .provider-voyage  { background: #1a1a2e; color: #8080e0; }
  .provider-openai  { background: #0d2a1f; color: #41d6a0; }
  .provider-rexbert { background: #1a0d1a; color: #d070d0; }
  .provider-custom  { background: #1a1a0d; color: #c0b040; }

  .dim-warn {
    display: flex; align-items: flex-start; gap: 10px;
    background: #1a1200; border: 1px solid #3a2e00; border-radius: 8px;
    padding: 12px 14px; font-size: 12px; color: #c0960a; line-height: 1.5;
  }
  .warn-icon { font-size: 14px; flex-shrink: 0; margin-top: 1px; }

  .model-editor-actions { display: flex; gap: 8px; justify-content: flex-end; }
  .btn-cancel {
    background: none; border: 1px solid var(--border-2); border-radius: 6px;
    color: var(--text-4); font-size: 12px; padding: 6px 16px; cursor: pointer;
  }
  .btn-cancel:hover:not(:disabled) { border-color: var(--text-5); color: var(--text-2); }
  .btn-save {
    background: var(--accent); border: none; border-radius: 6px;
    color: #fff; font-size: 12px; font-weight: 600; padding: 6px 18px; cursor: pointer;
  }
  .btn-save:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-save:disabled, .btn-cancel:disabled { opacity: 0.4; cursor: not-allowed; }
  .model-saved { font-size: 12px; color: #5cdb85; margin: 0; }

  .toggle-switch {
    position: relative; display: inline-block; width: 36px; height: 20px; cursor: pointer;
  }
  .toggle-switch input { opacity: 0; width: 0; height: 0; }
  .toggle-slider {
    position: absolute; inset: 0; background: var(--border-3); border-radius: 20px;
    transition: background 0.2s;
  }
  .toggle-slider::before {
    content: ''; position: absolute; width: 14px; height: 14px;
    left: 3px; top: 3px; background: var(--text-4); border-radius: 50%;
    transition: transform 0.2s, background 0.2s;
  }
  .toggle-switch input:checked + .toggle-slider { background: var(--accent); }
  .toggle-switch input:checked + .toggle-slider::before { transform: translateX(16px); background: #fff; }
  .toggle-switch input:disabled + .toggle-slider { opacity: 0.4; cursor: not-allowed; }
  .toggle-label { font-size: 12px; color: var(--text-4); min-width: 22px; }
</style>
