<script lang="ts">
  /**
   * IndexDetail — tabbed detail view for a single ISP index.
   *
   * Shown when the user navigates to the configure view from SourceContent.
   * Tabs: Overview (connectors + ingest status + index settings), Documents, Search, API.
   *
   * The Overview tab includes an inline embedding-model editor that lets the user
   * change the per-index model without leaving the page.  A rebuild warning is shown
   * whenever the selected model produces a different vector dimension than the current one.
   *
   * Dispatches ``back`` when the breadcrumb "← Indexes" button is clicked.
   */
  import { onMount } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { indexService } from '../../lib/services/indexes/IndexService';
  import { settingsService } from '../../lib/services/settings/SettingsService';
  import type { Index, IngestStatus } from '../../lib/services/indexes/index.model';
  import type { EmbedModelOption, SitemapConnectorConfig } from '../../lib/types';
  import DocumentTable from './DocumentTable.svelte';
  import SearchPanel from './SearchPanel.svelte';
  import ApiPanel from './ApiPanel.svelte';
  import { fmtDate } from './IndexDetail.helpers';

  export let indexId: string;
  export let tab: 'overview' | 'connector' | 'documents' | 'search' | 'api' = 'overview';

  const tabs: Array<'overview' | 'connector' | 'documents' | 'search' | 'api'> = ['overview', 'connector', 'documents', 'search', 'api'];

  const dispatch = createEventDispatcher<{ back: void }>();

  let meta: Index | null = null;
  let status: IngestStatus | null = null;

  // ── Embedding model editor ────────────────────────────────────────────────────

  /** Full list of models from the settings API, used to populate the picker. */
  let availableModels: EmbedModelOption[] = [];

  /** Currently selected model id in the editor (mirrors meta.embedModel on load). */
  let selectedEmbedModel = '';

  /** Whether the model editor is expanded. */
  let editingModel = false;

  let modelSaving = false;
  let modelSaved = false;
  let modelErr = '';

  /** Dimension of the model currently stored on the index. */
  $: currentDim = availableModels.find(m => m.id === (meta?.embedModel || ''))?.num_dim ?? 0;

  /** Dimension of the model the user has selected in the editor. */
  $: selectedDim = availableModels.find(m => m.id === selectedEmbedModel)?.num_dim ?? 0;

  /** True when the selected model produces a different vector size than the stored one. */
  $: dimWillChange = selectedDim > 0 && currentDim > 0 && selectedDim !== currentDim;

  $: isModelDirty = meta !== null && selectedEmbedModel !== (meta.embedModel ?? '');

  onMount(async () => {
    const [fetchedMeta, fetchedStatus, settings] = await Promise.all([
      indexService.getIndex(indexId),
      indexService.getStatus(indexId),
      settingsService.getSettings(),
    ]);
    meta = fetchedMeta;
    status = fetchedStatus;
    availableModels = settings.available_models ?? [];
    selectedEmbedModel = meta.embedModel ?? '';

    // Initialise connector form state once from the fetched meta.
    const c = fetchedMeta.connectors[0] ?? {};
    connectorType    = (c.type ?? '') as 'rest' | 'sitemap' | '';
    sitemapUrl       = String(c.url ?? '');
    sitemapMaxPages  = Number(c.max_pages ?? 50);
    sitemapSelector  = String(c.content_selector ?? '');
    sitemapUserAgent = String(c.user_agent ?? '');
    includePatterns  = ((c.include_patterns ?? []) as string[]).map(v => ({ value: v }));
    excludePatterns  = ((c.exclude_patterns ?? []) as string[]).map(v => ({ value: v }));
    metaFields       = Object.entries((c.meta_fields ?? {}) as Record<string,string>).map(([key, value]) => ({ key, value }));
  });

  /** Save the selected embedding model to the index. */
  async function saveEmbedModel() {
    if (!meta || !isModelDirty) return;
    modelSaving = true; modelErr = ''; modelSaved = false;
    try {
      const updated = await indexService.setIndexEmbedModel(indexId, selectedEmbedModel);
      meta = updated;
      selectedEmbedModel = updated.embedModel ?? '';
      modelSaved = true;
      editingModel = false;
    } catch (e) {
      modelErr = String(e);
    } finally {
      modelSaving = false;
    }
  }

  function cancelEditModel() {
    selectedEmbedModel = meta?.embedModel ?? '';
    editingModel = false;
    modelErr = '';
    modelSaved = false;
  }

  // ── Version boost toggle ──────────────────────────────────────────────────────

  let versionBoostSaving = false;
  let versionBoostErr = '';

  async function toggleVersionBoost() {
    if (!meta) return;
    versionBoostSaving = true; versionBoostErr = '';
    try {
      meta = await indexService.setVersionBoost(indexId, !meta.versionBoost);
    } catch (e) {
      versionBoostErr = String(e);
    } finally {
      versionBoostSaving = false;
    }
  }

  // ── Connector editor ──────────────────────────────────────────────────────────
  // Plain `let` — initialized once in onMount. Must NOT be $: reactive or
  // Svelte will re-derive from meta on every reactivity cycle, wiping user edits.

  let connectorType: 'rest' | 'sitemap' | '' = '';
  let sitemapUrl = '';
  let sitemapMaxPages = 50;
  let sitemapSelector = '';
  let sitemapUserAgent = '';
  let includePatterns: { value: string }[] = [];
  let excludePatterns: { value: string }[] = [];
  let metaFields: { key: string; value: string }[] = [];

  let connectorSaving = false;
  let connectorErr = '';
  let connectorSaved = false;

  function addIncludePattern() { includePatterns = [...includePatterns, { value: '' }]; }
  function removeIncludePattern(i: number) { includePatterns = includePatterns.filter((_, idx) => idx !== i); }
  function addExcludePattern() { excludePatterns = [...excludePatterns, { value: '' }]; }
  function removeExcludePattern(i: number) { excludePatterns = excludePatterns.filter((_, idx) => idx !== i); }
  function addMetaField() { metaFields = [...metaFields, { key: '', value: '' }]; }
  function removeMetaField(i: number) { metaFields = metaFields.filter((_, idx) => idx !== i); }

  async function saveConnector() {
    connectorSaving = true; connectorErr = ''; connectorSaved = false;
    try {
      if (connectorType === 'sitemap') {
        const cfg: SitemapConnectorConfig = {
          url: sitemapUrl.trim(),
          max_pages: sitemapMaxPages,
          content_selector: sitemapSelector.trim(),
          user_agent: sitemapUserAgent.trim(),
          include_patterns: includePatterns.map(r => r.value.trim()).filter(Boolean),
          exclude_patterns: excludePatterns.map(r => r.value.trim()).filter(Boolean),
          meta_fields: Object.fromEntries(metaFields.filter(r => r.key.trim() && r.value.trim()).map(r => [r.key.trim(), r.value.trim()])),
        };
        await indexService.saveSitemapConnector(indexId, cfg);
      }
      connectorSaved = true;
      setTimeout(() => { connectorSaved = false; }, 2000);
    } catch (e) {
      connectorErr = String(e);
    } finally {
      connectorSaving = false;
    }
  }
</script>

<div class="detail">
  <div class="breadcrumb">
    <button on:click={() => dispatch('back')}>← Indexes</button>
    {#if meta}<span>/ {meta.name}</span>{/if}
  </div>

  {#if !meta}
    <div class="loading">Loading…</div>
  {:else}
    <div class="detail-head">
      <div>
        <h1>{meta.name}</h1>
        {#if meta.description}<p class="desc">{meta.description}</p>{/if}
      </div>
      <div class="stats">
        <div class="stat">
          <span class="stat-val">{meta.docCount.toLocaleString()}</span>
          <span class="stat-lbl">Documents</span>
        </div>
        <div class="stat">
          <span class="stat-val">{meta.connectors.length}</span>
          <span class="stat-lbl">Connectors</span>
        </div>
        <div class="stat">
          <span class="stat-val">{fmtDate(meta.createdAt)}</span>
          <span class="stat-lbl">Created</span>
        </div>
      </div>
    </div>

    <div class="tabs">
      {#each tabs as t}
        <button class:active={tab === t} on:click={() => tab = t}>
          {t.charAt(0).toUpperCase() + t.slice(1)}
        </button>
      {/each}
    </div>

    <div class="tab-body">
      {#if tab === 'overview'}
        <div class="overview">

          <!-- Last ingest status -->
          {#if status && status.state !== 'idle'}
            <div class="status-card" class:running={status.state === 'running'}
              class:done={status.state === 'done'} class:error={status.state === 'error'}>
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
                <div class="conn-url">{String(conn.url ?? '')}</div>
              </div>
            {/each}
          {:else}
            <div class="empty-connectors">
              No connectors configured. Use the ingest endpoints to add data to this index.
            </div>
          {/if}

          <!-- ── Index settings ──────────────────────────────────────────────── -->
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
                    <!-- "Use global default" option -->
                    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
                    <label
                      class="model-card"
                      class:model-card-selected={selectedEmbedModel === ''}
                    >
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
                      <label
                        class="model-card"
                        class:model-card-selected={selectedEmbedModel === m.id}
                      >
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
                    <button class="btn-cancel" on:click={cancelEditModel} disabled={modelSaving}>
                      Cancel
                    </button>
                    <button
                      class="btn-save-model"
                      on:click={saveEmbedModel}
                      disabled={modelSaving || !isModelDirty}
                    >
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

      {:else if tab === 'connector'}
        <div class="overview">
          {#if connectorType === 'sitemap'}
            <div class="setting-card connector-form">
              <label class="field">
                Sitemap URL
                <input bind:value={sitemapUrl} placeholder="https://example.com/sitemap.xml" />
              </label>
              <div class="field-row-2col">
                <label class="field">
                  Max pages <span class="hint-inline">(optional, default 50)</span>
                  <input type="number" bind:value={sitemapMaxPages} min="1" max="10000" />
                </label>
                <label class="field">
                  Content selector <span class="hint-inline">(optional)</span>
                  <input bind:value={sitemapSelector} placeholder="article, .post-content" />
                </label>
              </div>
              <label class="field">
                User Agent <span class="hint-inline">(optional)</span>
                <input bind:value={sitemapUserAgent} placeholder="Mozilla/5.0 ..." />
              </label>

              <div class="field">
                <span class="field-label-text">Include patterns <span class="hint-inline">(optional)</span></span>
                {#each includePatterns as row, i}
                  <div class="extra-row">
                    <input bind:value={row.value} placeholder="/support/" class="field-input" />
                    <button class="rm-btn" type="button" on:click={() => removeIncludePattern(i)}>✕</button>
                  </div>
                {/each}
                <button type="button" class="add-mapping-btn" on:click={addIncludePattern}>+ Add include pattern</button>
              </div>

              <div class="field">
                <span class="field-label-text">Exclude patterns <span class="hint-inline">(optional)</span></span>
                {#each excludePatterns as row, i}
                  <div class="extra-row">
                    <input bind:value={row.value} placeholder="/tag/" class="field-input" />
                    <button class="rm-btn" type="button" on:click={() => removeExcludePattern(i)}>✕</button>
                  </div>
                {/each}
                <button type="button" class="add-mapping-btn" on:click={addExcludePattern}>+ Add exclude pattern</button>
              </div>

              <div class="field">
                <span class="field-label-text">Meta field mappings <span class="hint-inline">(optional)</span></span>
                {#each metaFields as row, i}
                  <div class="extra-row">
                    <input bind:value={row.key} placeholder="og:site_name" class="field-input" />
                    <span style="color:#555">→</span>
                    <input bind:value={row.value} placeholder="site_name" class="field-input" />
                    <button class="rm-btn" type="button" on:click={() => removeMetaField(i)}>✕</button>
                  </div>
                {/each}
                <button type="button" class="add-mapping-btn" on:click={addMetaField}>+ Add field</button>
              </div>

              {#if connectorErr}<p class="err">{connectorErr}</p>{/if}
              <div class="connector-actions">
                <button class="btn-save-model" on:click={saveConnector} disabled={connectorSaving}>
                  {connectorSaving ? 'Saving…' : connectorSaved ? '✓ Saved' : 'Save connector'}
                </button>
              </div>
            </div>
          {:else}
            <p class="no-connector">No editable connector settings for this index type.</p>
          {/if}
        </div>

      {:else if tab === 'documents'}
        <DocumentTable {indexId} />

      {:else if tab === 'search'}
        <SearchPanel {indexId} />

      {:else if tab === 'api'}
        <ApiPanel {indexId} />
      {/if}
    </div>
  {/if}
</div>

<style>
  .detail { display: flex; flex-direction: column; gap: 0; }
  .breadcrumb {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 20px; font-size: 13px; color: #666;
  }
  .breadcrumb button {
    background: none; border: none; color: #7c6cf4; cursor: pointer; font-size: 13px;
  }
  .breadcrumb button:hover { text-decoration: underline; }
  .loading { color: #555; padding: 40px 0; text-align: center; }
  .detail-head {
    display: flex; justify-content: space-between; align-items: flex-start;
    margin-bottom: 24px; flex-wrap: wrap; gap: 16px;
  }
  h1 { margin: 0 0 4px; font-size: 22px; color: #eee; }
  .desc { margin: 0; font-size: 13px; color: #666; }
  .stats { display: flex; gap: 24px; }
  .stat { display: flex; flex-direction: column; align-items: flex-end; }
  .stat-val { font-size: 18px; font-weight: 600; color: #eee; }
  .stat-lbl { font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 0.5px; }

  .tabs { display: flex; border-bottom: 1px solid #1e1e1e; margin-bottom: 24px; }
  .tabs button {
    background: none; border: none; border-bottom: 2px solid transparent;
    padding: 10px 16px; color: #666; font-size: 13px; cursor: pointer; margin-bottom: -1px;
  }
  .tabs button.active { color: #7c6cf4; border-bottom-color: #7c6cf4; }
  .tabs button:hover:not(.active) { color: #aaa; }
  .tab-body { min-height: 200px; }

  .overview { display: flex; flex-direction: column; gap: 20px; }
  .no-connector { color: #555; font-size: 13px; margin: 0; }
  h2 { font-size: 13px; color: #666; font-weight: 600; margin: 0;
       text-transform: uppercase; letter-spacing: 0.06em; }

  /* Ingest status */
  .status-card {
    background: #0f1a15; border: 1px solid #1e3028; border-radius: 8px; padding: 14px 16px;
  }
  .status-card.running { background: #0f1520; border-color: #1e2a40; }
  .status-card.error   { background: #1a0f0f; border-color: #3a1e1e; }
  .status-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
  .status-label { font-size: 12px; color: #666; }
  .status-badge {
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    padding: 2px 7px; border-radius: 4px; background: #1e3028; color: #5cdb85;
  }
  .status-card.running .status-badge { background: #1e2a40; color: #70a8ff; }
  .status-card.error   .status-badge { background: #3a1e1e; color: #e55; }
  .status-card p { margin: 0; font-size: 12px; color: #777; }
  .err { color: #e55 !important; font-size: 12px; margin: 6px 0 0; }

  /* Connectors */
  .connector-card {
    background: #111; border: 1px solid #1e1e1e; border-radius: 8px;
    padding: 12px 14px; display: flex; gap: 12px; align-items: center;
  }
  .conn-type {
    background: #1e1b3a; color: #9d91f5; font-size: 11px; font-weight: 600;
    text-transform: uppercase; padding: 2px 7px; border-radius: 4px; flex-shrink: 0;
  }
  .conn-url { font-size: 12px; color: #666; word-break: break-all; }
  .empty-connectors { font-size: 13px; color: #555; text-align: center; padding: 32px 0; }

  /* ── Index settings section ───────────────────────────────────────────────── */
  .settings-section { display: flex; flex-direction: column; gap: 12px; }

  .setting-card {
    background: #0e0e0e; border: 1px solid #1e1e1e; border-radius: 10px; padding: 16px 18px;
    display: flex; flex-direction: column; gap: 12px;
  }
  .setting-header {
    display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;
  }
  .setting-title-group { display: flex; flex-direction: column; gap: 3px; }
  .setting-title { font-size: 13px; font-weight: 600; color: #ddd; }
  .setting-hint { font-size: 12px; color: #555; max-width: 480px; line-height: 1.5; }
  .setting-current { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }

  /* Current model badges */
  .embed-model-badge {
    font-size: 11px; font-weight: 600; padding: 3px 9px;
    border-radius: 10px; letter-spacing: 0.02em;
  }
  .embed-model-badge-custom  { background: #1e2a40; color: #70a8ff; }
  .embed-model-badge-default { background: #1a1a1a; color: #666; }

  .btn-edit {
    background: none; border: 1px solid #333; border-radius: 6px;
    color: #888; font-size: 12px; padding: 4px 12px; cursor: pointer;
  }
  .btn-edit:hover { border-color: #555; color: #ccc; }

  /* Model picker grid */
  .model-editor { display: flex; flex-direction: column; gap: 12px; }
  .model-grid { display: flex; flex-direction: column; gap: 8px; }

  .model-card {
    display: flex; align-items: flex-start; gap: 12px;
    background: #111; border: 1px solid #222; border-radius: 8px;
    padding: 12px 14px; cursor: pointer; transition: border-color 0.15s;
  }
  .model-card:hover { border-color: #444; }
  .model-card-selected { border-color: #7c6cf4; background: #12103a; }

  .model-radio { margin-top: 2px; accent-color: #7c6cf4; flex-shrink: 0; }
  .model-card-info { display: flex; flex-direction: column; gap: 4px; }
  .model-card-top { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
  .model-card-name { font-size: 13px; font-weight: 600; color: #ddd; }
  .model-card-desc { font-size: 12px; color: #555; line-height: 1.4; }

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
    background: none; border: 1px solid #2a2a2a; border-radius: 6px;
    color: #666; font-size: 12px; padding: 6px 16px; cursor: pointer;
  }
  .btn-cancel:hover:not(:disabled) { border-color: #444; color: #aaa; }
  .btn-save-model {
    background: #7c6cf4; border: none; border-radius: 6px;
    color: #fff; font-size: 12px; font-weight: 600; padding: 6px 18px; cursor: pointer;
  }
  .btn-save-model:hover:not(:disabled) { background: #9580ff; }
  .btn-save-model:disabled, .btn-cancel:disabled { opacity: 0.4; cursor: not-allowed; }

  .model-saved { font-size: 12px; color: #5cdb85; margin: 0; }

  /* Connector editor form */
  .connector-form { display: flex; flex-direction: column; gap: 11px; }
  .field-row-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .field {
    display: flex; flex-direction: column; gap: 5px;
    font-size: 12px; color: #777;
  }
  .field input {
    background: #111; border: 1px solid #252525; border-radius: 6px;
    padding: 7px 10px; color: #ddd; font-size: 12px; font-family: inherit;
  }
  .field input:focus { outline: none; border-color: #7c6cf4; }
  .hint-inline { font-size: 10px; color: #555; font-weight: 400; }
  .field-label-text { display: block; font-size: 12px; color: #777; margin-bottom: 4px; }
  .extra-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
  .field-input {
    flex: 1; background: #111; border: 1px solid #252525; border-radius: 6px;
    padding: 6px 9px; color: #ddd; font-size: 12px; font-family: inherit;
  }
  .field-input:focus { outline: none; border-color: #7c6cf4; }
  .rm-btn {
    background: none; border: 1px solid #2a2a2a; border-radius: 5px;
    color: #555; font-size: 11px; padding: 4px 7px; cursor: pointer; flex-shrink: 0;
  }
  .rm-btn:hover { border-color: #e55; color: #e55; }
  .add-mapping-btn {
    background: none; border: 1px dashed #2a2a2a; border-radius: 5px;
    color: #555; font-size: 11px; padding: 5px 12px; cursor: pointer; margin-top: 2px;
  }
  .add-mapping-btn:hover { border-color: #7c6cf4; color: #9d91f5; }
  .connector-actions { display: flex; justify-content: flex-end; padding-top: 4px; }

  /* Version boost toggle */
  .toggle-switch {
    position: relative; display: inline-block; width: 36px; height: 20px; cursor: pointer;
  }
  .toggle-switch input { opacity: 0; width: 0; height: 0; }
  .toggle-slider {
    position: absolute; inset: 0; background: #2a2a2a; border-radius: 20px;
    transition: background 0.2s;
  }
  .toggle-slider::before {
    content: ''; position: absolute; width: 14px; height: 14px;
    left: 3px; top: 3px; background: #888; border-radius: 50%;
    transition: transform 0.2s, background 0.2s;
  }
  .toggle-switch input:checked + .toggle-slider { background: #7c6cf4; }
  .toggle-switch input:checked + .toggle-slider::before {
    transform: translateX(16px); background: #fff;
  }
  .toggle-switch input:disabled + .toggle-slider { opacity: 0.4; cursor: not-allowed; }
  .toggle-label { font-size: 12px; color: #888; min-width: 22px; }
</style>
