<script lang="ts">
  /**
   * CreateWizard — 6-step modal for onboarding a new ISP index.
   *
   * Step 1 — Name & description: creates the index record via the API.
   * Step 2 — Connector: configure REST API or Sitemap source and credentials.
   * Step 3 — Field mapping: map source fields to ISP schema fields (id, title,
   *           content, url) and add optional extra field rows.
   * Step 4 — Extensions: optionally select JS transform extensions to apply
   *           during indexing; selected extensions are assigned (in displayed
   *           order) before the ingest job is queued.
   * Step 5 — Embedding model: choose which embedding model to use for this
   *           index.  Selecting a model here updates the global setting before
   *           ingestion begins; a dimension-change warning is shown when the
   *           new model would break existing indexes.
   * Step 6 — Progress: starts the ingest job and polls until done.
   *
   * Dispatches ``done`` (with the new index id) on completion and ``cancel`` on dismiss.
   */
  import { createEventDispatcher } from 'svelte';
  import { api } from '../lib/api';
  import type { RestConnectorConfig, SitemapConnectorConfig, ExtensionMeta, EmbedModelOption, AppSettings } from '../lib/types';

  const dispatch = createEventDispatcher<{ done: string; cancel: void }>();

  let step = 1;
  let busy = false;
  let errMsg = '';

  // ── Step 1: Name & description ───────────────────────────────────────────────
  let name = '';
  let description = '';
  let indexId = '';

  // Index type — ISP-managed (private collection) vs external/catalog write-back
  let isExternal = false;
  let externalCollection = '';
  let externalFilter = '';
  let externalQueryBy = 'display';
  let externalTitleField = 'display';
  let externalUrlField = '';

  // ── Step 2: Connector ────────────────────────────────────────────────────────
  let connectorType: 'rest' | 'sitemap' = 'rest';

  // REST fields
  let restUrl = '';
  let restMethod = 'GET';
  let restHeaders = '';
  let restBody = '';
  const headersPlaceholder = '{"Authorization": "Bearer token"}';
  const bodyPlaceholder = '{"key": "value"}';

  // Sitemap fields
  let sitemapUrl = '';
  let sitemapMaxPages = 100;
  let sitemapSelector = '';
  let sitemapInclude = '';
  let sitemapExclude = '';

  // ── Step 3: Field mapping ────────────────────────────────────────────────────
  let resultPath = '';
  let idField = '';
  let titleField = '';
  let contentField = '';
  let urlField = '';
  /** Extra arbitrary source→target field rows added by the user. */
  let extraMappings: Array<{ src: string; tgt: string }> = [];

  /** Append a blank source→target row to the extra field mappings list. */
  function addMapping() {
    extraMappings = [...extraMappings, { src: '', tgt: '' }];
  }

  /** Remove the extra mapping row at index ``i``. */
  function removeMapping(i: number) {
    extraMappings = extraMappings.filter((_, idx) => idx !== i);
  }

  const TARGET_SUGGESTIONS = ['title', 'content', 'url', 'id', 'source_type', 'category', 'price', 'manufacturer', 'brand', 'tags'];

  // ── Step 4: Extensions ───────────────────────────────────────────────────────

  /** Full list of available extensions fetched from the global library. */
  let extensionsList: ExtensionMeta[] = [];

  /** Set of extension ids the user has checked — these will be assigned to the index. */
  let selectedExtIds = new Set<string>();

  let extensionsLoading = false;
  let extensionsErr = '';

  /**
   * Toggle an extension's selection state.
   * Svelte reactivity requires re-assigning the Set to trigger a re-render.
   *
   * @param id  Extension id to toggle.
   */
  function toggleExt(id: string) {
    const next = new Set(selectedExtIds);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    selectedExtIds = next;
  }

  /**
   * Fetch the global extension library and store it in ``extensionsList``.
   * Called when the user enters step 4 for the first time (or if a previous
   * load failed).  Sets ``extensionsLoading`` and ``extensionsErr`` accordingly.
   */
  async function loadExtensions() {
    extensionsLoading = true;
    extensionsErr = '';
    try {
      extensionsList = await api.listExtensions();
    } catch (e) {
      extensionsErr = String(e);
    } finally {
      extensionsLoading = false;
    }
  }

  // ── Step 5: Embedding model ──────────────────────────────────────────────────

  /** Loaded settings (available models + current active model). */
  let embedSettings: AppSettings | null = null;

  /** Model id the user has selected in step 5. Pre-populated with the global active model. */
  let selectedEmbedModel = '';

  let embedLoading = false;
  let embedErr = '';

  /**
   * Fetch the current global settings to populate the model selector.
   * Called when the user enters step 5 for the first time.
   */
  async function loadEmbedSettings() {
    embedLoading = true;
    embedErr = '';
    try {
      embedSettings = await api.getSettings();
      selectedEmbedModel = embedSettings.embed_model;
    } catch (e) {
      embedErr = String(e);
    } finally {
      embedLoading = false;
    }
  }

  /**
   * Return the ``EmbedModelOption`` object for a given model id, or ``null``.
   * Used to display the dimension and provider badges in the model card.
   *
   * @param id  Model id to look up.
   */
  function embedModelOption(id: string): EmbedModelOption | null {
    return embedSettings?.available_models.find(m => m.id === id) ?? null;
  }

  /**
   * Return true when the selected model's dimension differs from the current
   * global model's dimension — used to show a pre-save warning in step 5.
   */
  function embedDimWillChange(): boolean {
    if (!embedSettings) return false;
    const cur  = embedModelOption(embedSettings.embed_model);
    const next = embedModelOption(selectedEmbedModel);
    return !!cur && !!next && cur.num_dim > 0 && next.num_dim > 0 && cur.num_dim !== next.num_dim;
  }

  /** True when the user has selected a model different from the stored global model. */
  $: embedIsDirty = embedSettings !== null && selectedEmbedModel !== embedSettings.embed_model;

  // ── Step 6: Progress ─────────────────────────────────────────────────────────
  let statusText = 'Queued...';
  let statusIndexed = 0;
  let statusTotal = 0;

  // ── Step transitions ─────────────────────────────────────────────────────────

  /**
   * Validate the name field, create the index record via the API, then advance to step 2.
   */
  async function stepOne() {
    if (!name.trim()) { errMsg = 'Name is required'; return; }
    if (isExternal && !externalCollection.trim()) { errMsg = 'Collection name is required for catalog write-back'; return; }
    busy = true; errMsg = '';
    try {
      const meta = await api.createIndex(name.trim(), description.trim(), {
        is_external: isExternal,
        external_collection: externalCollection.trim(),
        external_filter: externalFilter.trim(),
        external_query_by: externalQueryBy.trim() || 'display',
        external_title_field: externalTitleField.trim() || 'display',
        external_url_field: externalUrlField.trim(),
      });
      indexId = meta.id;
      step = 2;
    } catch (e) {
      errMsg = String(e);
    } finally {
      busy = false;
    }
  }

  /**
   * Validate that the connector URL is filled, then advance to step 3 (field mapping).
   */
  function stepTwo() {
    errMsg = '';
    if (connectorType === 'rest' && !restUrl.trim()) { errMsg = 'URL is required'; return; }
    if (connectorType === 'sitemap' && !sitemapUrl.trim()) { errMsg = 'Sitemap URL is required'; return; }
    step = 3;
  }

  /**
   * Validate field mapping (no required fields — all optional) and advance to step 4.
   * Triggers the extension library load in the background so the list is ready
   * by the time the user reaches step 4.
   */
  function stepThree() {
    errMsg = '';
    step = 4;
    if (extensionsList.length === 0 && !extensionsErr) {
      loadExtensions();
    }
  }

  /**
   * Advance from Extensions (step 4) to Embedding Model (step 5).
   * Triggers the settings load in the background so the model list is ready
   * by the time the user reaches step 5.
   */
  function stepFour() {
    errMsg = '';
    step = 5;
    if (!embedSettings && !embedLoading) {
      loadEmbedSettings();
    }
  }

  /**
   * Finalise the wizard and start ingestion.
   *
   * 1. If the user picked a different embedding model, persist it globally via
   *    PUT /v1/settings so it takes effect before the ingest job runs.
   * 2. Assign any selected extensions to the index in display order.
   * 3. Build the connector config, start the ingest job, advance to step 6,
   *    and begin polling for completion.
   *
   * Extension assignment failures are silently ignored so ingestion can proceed.
   * Model-update failures surface as an error (the user can still go back and
   * keep the current model).
   */
  async function stepFive() {
    busy = true; errMsg = '';
    try {
      // 1. Update global embedding model if the user changed it
      if (embedIsDirty && selectedEmbedModel) {
        await api.updateSettings(selectedEmbedModel);
      }

      // 2. Assign selected extensions in display order
      const ordered = extensionsList.filter(e => selectedExtIds.has(e.id));
      await Promise.allSettled(
        ordered.map((ext, i) => api.assignExtension(indexId, ext.id, i + 1, true))
      );

      // 3. Build field_map from extra rows (skip incomplete rows)
      const field_map: Record<string, string> = {};
      for (const row of extraMappings) {
        if (row.src.trim()) field_map[row.src.trim()] = row.tgt.trim();
      }

      if (connectorType === 'rest') {
        let parsedHeaders: Record<string, string> = {};
        if (restHeaders.trim()) {
          try { parsedHeaders = JSON.parse(restHeaders); } catch { /* ignore */ }
        }
        const cfg: RestConnectorConfig & {
          result_path?: string; id_field?: string; title_field?: string;
          content_field?: string; url_field?: string; field_map?: Record<string, string>;
        } = {
          url: restUrl.trim(),
          method: restMethod,
          headers: parsedHeaders,
          body: restBody.trim() || undefined,
          ...(resultPath.trim()   && { result_path:  resultPath.trim() }),
          ...(idField.trim()      && { id_field:      idField.trim() }),
          ...(titleField.trim()   && { title_field:   titleField.trim() }),
          ...(contentField.trim() && { content_field: contentField.trim() }),
          ...(urlField.trim()     && { url_field:     urlField.trim() }),
          ...(Object.keys(field_map).length && { field_map }),
        };
        await api.ingestRest(indexId, cfg);
      } else {
        const cfg: SitemapConnectorConfig = {
          url: sitemapUrl.trim(),
          max_pages: sitemapMaxPages,
          content_selector: sitemapSelector.trim(),
          include_pattern: sitemapInclude.trim(),
          exclude_pattern: sitemapExclude.trim(),
        };
        await api.ingestSitemap(indexId, cfg);
      }

      step = 6;
      pollUntilDone();
    } catch (e) {
      errMsg = String(e);
    } finally {
      busy = false;
    }
  }

  /**
   * Poll the ingest job status every 1.5 s until it reaches a terminal state.
   * Updates ``statusText``, ``statusIndexed``, and ``statusTotal`` to drive the step-5 UI.
   */
  async function pollUntilDone() {
    while (true) {
      await new Promise(r => setTimeout(r, 1500));
      try {
        const s = await api.getStatus(indexId);
        statusIndexed = s.indexed;
        statusTotal = s.total;
        if (s.state === 'running') {
          statusText = `Indexing ${s.indexed} / ${s.total || '…'} documents`;
        } else if (s.state === 'done') {
          statusText = `Done — ${s.indexed} documents indexed`;
          break;
        } else if (s.state === 'error') {
          statusText = `Error: ${s.error}`;
          errMsg = s.error;
          break;
        }
      } catch { /* ignore transient errors */ }
    }
  }
</script>

<div class="wizard">
  <div class="wizard-head">
    <h2>Create Index</h2>
    <button class="close" on:click={() => dispatch('cancel')}>✕</button>
  </div>

  <div class="steps">
    {#each [1,2,3,4,5,6] as s}
      <div class="step" class:active={step === s} class:done={step > s}>{s}</div>
    {/each}
  </div>

  <!-- Step 1: Name -->
  {#if step === 1}
    <div class="body">
      <label>Name <input bind:value={name} placeholder="My Knowledge Base" /></label>
      <label>Description <input bind:value={description} placeholder="Optional" /></label>

      <div class="index-type-section">
        <div class="field-group-label">Index type</div>
        <p class="index-type-desc">
          <strong>ISP Managed</strong> — ISP creates and owns a private Typesense collection for this index. Use this for any new data source (REST API, sitemap, etc.).<br/>
          <strong>External / Catalog</strong> — ISP writes into a pre-existing shared Typesense collection (e.g. the <code>catalog</code> collection used by Phones and Smartwatches). All rich product fields are stored flat on the document. Use this when the source belongs to the same product catalog.
        </p>
        <div class="type-toggle">
          <button class:sel={!isExternal} on:click={() => isExternal = false}>ISP Managed</button>
          <button class:sel={isExternal}  on:click={() => isExternal = true}>External / Catalog</button>
        </div>
      </div>

      {#if isExternal}
        <div class="ext-info-box">
          <p class="ext-info-text">
            Writes into a pre-existing Typesense collection shared across multiple indexes (e.g. the <code>catalog</code> collection used by Phones and Smartwatches). Field mapping is handled by the extension — not by ISP.
          </p>
        </div>
        <label>
          Collection name <span class="hint">(existing Typesense collection)</span>
          <input bind:value={externalCollection} placeholder="catalog" />
        </label>
        <label>
          Filter <span class="hint">(scopes this index within the collection)</span>
          <input bind:value={externalFilter} placeholder="type:=tablet" />
        </label>
        <label>
          Query-by field <span class="hint">(for search, default: display)</span>
          <input bind:value={externalQueryBy} placeholder="display" />
        </label>
        <label>
          Title field <span class="hint">(field shown as document title)</span>
          <input bind:value={externalTitleField} placeholder="display" />
        </label>
        <label>
          URL prefix <span class="hint">(prepended to url_name slug)</span>
          <input bind:value={externalUrlField} placeholder="https://www.t-mobile.com/cell-phone/" />
        </label>
      {:else}
        <div class="ext-info-box">
          <p class="ext-info-text">
            ISP provisions and owns a private Typesense collection for this index. Best for generic REST APIs, sitemaps, or any source that doesn't need to share a collection.
          </p>
        </div>
      {/if}

      {#if errMsg}<p class="err">{errMsg}</p>{/if}
      <div class="actions">
        <button class="btn-secondary" on:click={() => dispatch('cancel')}>Cancel</button>
        <button class="btn-primary" on:click={stepOne} disabled={busy}>
          {busy ? 'Creating…' : 'Next →'}
        </button>
      </div>
    </div>

  <!-- Step 2: Connector -->
  {:else if step === 2}
    <div class="body">
      <div class="type-toggle">
        <button class:sel={connectorType==='rest'} on:click={() => connectorType='rest'}>REST API</button>
        <button class:sel={connectorType==='sitemap'} on:click={() => connectorType='sitemap'}>Sitemap</button>
      </div>

      {#if connectorType === 'rest'}
        <label>Endpoint URL
          <input bind:value={restUrl} placeholder="https://api.example.com/data" />
        </label>
        <label>HTTP Method
          <select bind:value={restMethod}>
            <option>GET</option><option>POST</option>
          </select>
        </label>
        <label>Headers <span class="hint">(JSON object, optional)</span>
          <textarea bind:value={restHeaders} rows="3" placeholder={headersPlaceholder}></textarea>
        </label>
        <label>Request body <span class="hint">(JSON, for POST)</span>
          <textarea bind:value={restBody} rows="3" placeholder={bodyPlaceholder}></textarea>
        </label>
      {:else}
        <label>Sitemap URL
          <input bind:value={sitemapUrl} placeholder="https://example.com/sitemap.xml" />
        </label>
        <label>Max pages
          <input type="number" bind:value={sitemapMaxPages} min="1" max="10000" />
        </label>
        <label>Content CSS selector <span class="hint">(optional)</span>
          <input bind:value={sitemapSelector} placeholder="article, .post-content" />
        </label>
        <label>Include pattern <span class="hint">(URL must contain)</span>
          <input bind:value={sitemapInclude} placeholder="/blog/" />
        </label>
        <label>Exclude pattern <span class="hint">(URL must not contain)</span>
          <input bind:value={sitemapExclude} placeholder="/tag/" />
        </label>
      {/if}

      {#if errMsg}<p class="err">{errMsg}</p>{/if}
      <div class="actions">
        <button class="btn-secondary" on:click={() => step = 1}>← Back</button>
        <button class="btn-primary" on:click={stepTwo}>Next: Field Mapping →</button>
      </div>
    </div>

  <!-- Step 3: Field mapping -->
  {:else if step === 3}
    <div class="body">
      <div class="step-desc-block">
        <p class="step-desc">
          Map fields from your data source to ISP's internal fields. All mappings are optional — leave blank to auto-detect.
        </p>
        <div class="field-legend">
          <div class="legend-item">
            <span class="legend-badge src">Source field</span>
            <span class="legend-text">The field name as it appears in your API response (e.g. <code>productName</code>, <code>sku</code>, <code>body_html</code>).</span>
          </div>
          <div class="legend-item">
            <span class="legend-badge tgt">Target field</span>
            <span class="legend-text">The ISP field to store it as (e.g. <code>title</code>, <code>content</code>). Use the core shortcuts on the left, or add extra rows for additional fields.</span>
          </div>
        </div>
      </div>

      {#if connectorType === 'rest'}
        <label>Result path <span class="hint">(dot-path to the array, e.g. <code>data.items</code>)</span>
          <input bind:value={resultPath} placeholder="auto-detect" />
        </label>
      {/if}

      <div class="core-fields">
        <div class="core-grid">
          <span class="col-hdr">Source field</span>
          <span class="col-arrow"></span>
          <span class="col-hdr">ISP field</span>
        </div>
        <div class="core-row">
          <input bind:value={idField} placeholder="auto" class="field-input" />
          <span class="arrow">→</span>
          <span class="fixed-target">id</span>
        </div>
        <div class="core-row">
          <input bind:value={titleField} placeholder="auto" class="field-input" />
          <span class="arrow">→</span>
          <span class="fixed-target">title</span>
        </div>
        <div class="core-row">
          <input bind:value={contentField} placeholder="auto" class="field-input" />
          <span class="arrow">→</span>
          <span class="fixed-target">content</span>
        </div>
        <div class="core-row">
          <input bind:value={urlField} placeholder="auto" class="field-input" />
          <span class="arrow">→</span>
          <span class="fixed-target">url</span>
        </div>
      </div>

      {#if extraMappings.length > 0}
        <div class="extra-label">Extra fields</div>
        {#each extraMappings as row, i}
          <div class="extra-row">
            <input bind:value={row.src} placeholder="source field" class="field-input" />
            <span class="arrow">→</span>
            <input
              bind:value={row.tgt}
              placeholder="target field"
              class="field-input"
              list="target-suggestions"
            />
            <button class="rm-btn" on:click={() => removeMapping(i)}>✕</button>
          </div>
        {/each}
        <datalist id="target-suggestions">
          {#each TARGET_SUGGESTIONS as s}<option value={s} />{/each}
        </datalist>
      {/if}

      <button class="add-mapping-btn" on:click={addMapping}>+ Add field mapping</button>

      {#if errMsg}<p class="err">{errMsg}</p>{/if}
      <div class="actions">
        <button class="btn-secondary" on:click={() => step = 2}>← Back</button>
        <button class="btn-primary" on:click={stepThree}>Next: Extensions →</button>
      </div>
    </div>

  <!-- Step 4: Extensions -->
  {:else if step === 4}
    <div class="body">
      <div class="step-desc-block">
        <p class="step-desc">
          Select JS transform extensions to apply during indexing. Extensions run in the order shown and can reshape, enrich, or filter documents before they are stored.
        </p>
      </div>

      {#if extensionsLoading}
        <div class="ext-loading"><div class="spinner-sm"></div><span>Loading extensions…</span></div>

      {:else if extensionsErr}
        <p class="err">{extensionsErr}</p>
        <button class="add-mapping-btn" on:click={loadExtensions}>Retry</button>

      {:else if extensionsList.length === 0}
        <div class="ext-empty">
          <p>No extensions in the library yet.</p>
          <p class="ext-empty-hint">You can create extensions from the <strong>Extensions</strong> panel and return here to apply them.</p>
        </div>

      {:else}
        <div class="ext-list">
          {#each extensionsList as ext}
            <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
            <label
              class="ext-card"
              class:ext-card-selected={selectedExtIds.has(ext.id)}
            >
              <input
                type="checkbox"
                class="ext-check"
                checked={selectedExtIds.has(ext.id)}
                on:change={() => toggleExt(ext.id)}
              />
              <div class="ext-info">
                <span class="ext-name">{ext.name}</span>
                {#if ext.description}
                  <span class="ext-desc">{ext.description}</span>
                {/if}
              </div>
              {#if selectedExtIds.has(ext.id)}
                <span class="ext-order-badge">
                  #{[...extensionsList.filter(e => selectedExtIds.has(e.id))].indexOf(ext) + 1}
                </span>
              {/if}
            </label>
          {/each}
        </div>

        {#if selectedExtIds.size > 0}
          <p class="ext-summary">
            {selectedExtIds.size} extension{selectedExtIds.size > 1 ? 's' : ''} will be applied in the order shown above.
          </p>
        {/if}
      {/if}

      {#if errMsg}<p class="err">{errMsg}</p>{/if}
      <div class="actions">
        <button class="btn-secondary" on:click={() => step = 3}>← Back</button>
        <button class="btn-primary" on:click={stepFour} disabled={busy}>
          Next: Embedding Model →
        </button>
      </div>
    </div>

  <!-- Step 5: Embedding Model -->
  {:else if step === 5}
    <div class="body">
      <div class="step-desc-block">
        <p class="step-desc">
          Choose the embedding model used to vectorise documents in this index.
          This updates the global setting — all future ingests will use the selected
          model until changed in Settings.
        </p>
      </div>

      {#if embedLoading}
        <div class="ext-loading"><div class="spinner-sm"></div><span>Loading models…</span></div>

      {:else if embedErr}
        <p class="err">{embedErr}</p>
        <button class="add-mapping-btn" on:click={loadEmbedSettings}>Retry</button>

      {:else if embedSettings}
        <div class="embed-model-list">
          {#each embedSettings.available_models as m}
            <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
            <label
              class="embed-model-card"
              class:embed-model-card-selected={selectedEmbedModel === m.id}
            >
              <input
                type="radio"
                name="embed-model"
                class="embed-radio"
                value={m.id}
                bind:group={selectedEmbedModel}
              />
              <div class="embed-model-info">
                <div class="embed-model-top">
                  <span class="embed-model-name">{m.label}</span>
                  {#if m.num_dim > 0}
                    <span class="embed-dim-badge">{m.num_dim}-d</span>
                  {/if}
                  <span class="embed-provider-badge provider-{m.provider}">{m.provider}</span>
                  {#if m.id === embedSettings.embed_model}
                    <span class="embed-active-badge">active</span>
                  {/if}
                </div>
                <span class="embed-model-desc">{m.description}</span>
              </div>
            </label>
          {/each}
        </div>

        {#if embedDimWillChange()}
          <div class="embed-dim-warn">
            Dimension change detected — existing indexes will need a full rebuild before semantic search works.
          </div>
        {/if}
      {/if}

      {#if errMsg}<p class="err">{errMsg}</p>{/if}
      <div class="actions">
        <button class="btn-secondary" on:click={() => step = 4}>← Back</button>
        <button class="btn-primary" on:click={stepFive} disabled={busy}>
          {busy ? 'Starting…' : 'Start Ingestion →'}
        </button>
      </div>
    </div>

  <!-- Step 6: Progress -->
  {:else}
    <div class="body center">
      {#if errMsg}
        <p class="err">{errMsg}</p>
      {:else}
        {#if !statusText.startsWith('Done')}
          <div class="spinner"></div>
        {/if}
        <p class="status">{statusText}</p>
        {#if statusTotal > 0}
          <div class="progress-bar">
            <div class="fill" style="width:{(statusIndexed/statusTotal*100).toFixed(0)}%"></div>
          </div>
        {/if}
      {/if}
      {#if statusText.startsWith('Done') || errMsg}
        <button class="btn-primary" on:click={() => dispatch('done', indexId)}>View Index →</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .wizard {
    background: #141414;
    border: 1px solid #2a2a2a;
    border-radius: 12px;
    width: 520px;
    max-width: 95vw;
    max-height: 90vh;
    overflow-y: auto;
  }
  .wizard-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 20px 24px 0;
  }
  h2 { margin: 0; font-size: 17px; color: #eee; }
  .close {
    background: none; border: none; color: #555; cursor: pointer; font-size: 16px;
  }
  .steps {
    display: flex;
    gap: 0;
    padding: 16px 24px;
    align-items: center;
  }
  .step {
    width: 28px; height: 28px;
    border-radius: 50%;
    background: #222;
    color: #555;
    font-size: 12px;
    font-weight: 600;
    display: flex; align-items: center; justify-content: center;
  }
  .step + .step { margin-left: 12px; }
  .step.active { background: #7c6cf4; color: #fff; }
  .step.done { background: #2d5a3d; color: #5cdb85; }

  .body { padding: 0 24px 24px; display: flex; flex-direction: column; gap: 14px; }
  .body.center { align-items: center; text-align: center; padding-top: 12px; }

  .step-desc-block { display: flex; flex-direction: column; gap: 10px; }
  .step-desc { margin: 0; font-size: 12px; color: #666; }
  .field-legend {
    display: flex;
    flex-direction: column;
    gap: 6px;
    background: #0f0f0f;
    border: 1px solid #1e1e1e;
    border-radius: 7px;
    padding: 10px 12px;
  }
  .legend-item { display: flex; align-items: flex-start; gap: 10px; }
  .legend-badge {
    flex-shrink: 0;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 2px 7px;
    border-radius: 4px;
    margin-top: 1px;
  }
  .legend-badge.src { background: #1a1a2e; color: #7c6cf4; border: 1px solid #2e2b55; }
  .legend-badge.tgt { background: #0f1f16; color: #5cdb85; border: 1px solid #1e3d29; }
  .legend-text { font-size: 11px; color: #666; line-height: 1.5; }
  .legend-text code { font-size: 10px; background: #1e1e1e; padding: 1px 4px; border-radius: 3px; color: #9d91f5; }

  label {
    display: flex; flex-direction: column; gap: 6px;
    font-size: 13px; color: #999;
  }
  label input, label select, label textarea {
    background: #1a1a1a;
    border: 1px solid #2e2e2e;
    border-radius: 6px;
    padding: 8px 10px;
    color: #eee;
    font-size: 13px;
    font-family: inherit;
    resize: vertical;
  }
  label input:focus, label select:focus, label textarea:focus {
    outline: none; border-color: #7c6cf4;
  }
  label code { font-size: 10px; background: #1e1e1e; padding: 1px 4px; border-radius: 3px; color: #9d91f5; }
  .hint { font-size: 11px; color: #555; font-weight: 400; }

  /* Core fields grid */
  .core-fields {
    display: flex;
    flex-direction: column;
    gap: 6px;
    background: #111;
    border: 1px solid #222;
    border-radius: 8px;
    padding: 10px 12px;
  }
  .core-grid {
    display: grid;
    grid-template-columns: 1fr 28px 90px;
    gap: 6px;
    padding-bottom: 4px;
    border-bottom: 1px solid #1e1e1e;
    margin-bottom: 2px;
  }
  .col-hdr { font-size: 10px; font-weight: 700; color: #444; text-transform: uppercase; letter-spacing: 0.5px; }
  .core-row {
    display: grid;
    grid-template-columns: 1fr 28px 90px;
    gap: 6px;
    align-items: center;
  }
  .field-input {
    background: #1a1a1a;
    border: 1px solid #2e2e2e;
    border-radius: 5px;
    padding: 6px 8px;
    color: #eee;
    font-size: 12px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    min-width: 0;
  }
  .field-input:focus { outline: none; border-color: #7c6cf4; }
  .arrow { color: #444; font-size: 13px; text-align: center; }
  .fixed-target {
    font-size: 11px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    color: #6b5ce7;
    background: #16142a;
    padding: 4px 8px;
    border-radius: 4px;
    text-align: center;
  }

  /* Extra mappings */
  .extra-label { font-size: 11px; color: #555; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }
  .extra-row {
    display: grid;
    grid-template-columns: 1fr 28px 1fr 24px;
    gap: 6px;
    align-items: center;
  }
  .rm-btn {
    background: none; border: none; color: #444; cursor: pointer;
    font-size: 11px; padding: 4px;
    border-radius: 4px;
    line-height: 1;
  }
  .rm-btn:hover { background: #2a0f0f; color: #e55; }

  .add-mapping-btn {
    background: none;
    border: 1px dashed #2e2e2e;
    color: #555;
    border-radius: 6px;
    padding: 7px 12px;
    font-size: 12px;
    cursor: pointer;
    text-align: left;
    transition: border-color 0.15s, color 0.15s;
  }
  .add-mapping-btn:hover { border-color: #7c6cf4; color: #9d91f5; }

  /* ── Extensions step ──────────────────────────────────────────────────────── */

  .ext-loading {
    display: flex;
    align-items: center;
    gap: 10px;
    color: #555;
    font-size: 13px;
    padding: 12px 0;
  }
  .spinner-sm {
    width: 16px; height: 16px;
    border: 2px solid #222; border-top-color: #7c6cf4;
    border-radius: 50%; animation: spin 0.7s linear infinite;
    flex-shrink: 0;
  }

  .ext-empty {
    background: #0f0f0f;
    border: 1px solid #1e1e1e;
    border-radius: 8px;
    padding: 16px;
    text-align: center;
  }
  .ext-empty p { margin: 0 0 6px; font-size: 13px; color: #555; }
  .ext-empty-hint { font-size: 12px; color: #444; }
  .ext-empty strong { color: #666; }

  .ext-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .ext-card {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    background: #0f0f0f;
    border: 1px solid #1e1e1e;
    border-radius: 8px;
    padding: 10px 12px;
    cursor: pointer;
    transition: border-color 0.15s, background 0.1s;
    /* override the generic label rule */
    flex-direction: row !important;
  }
  .ext-card:hover { background: #131313; border-color: #2e2e2e; }
  .ext-card-selected { border-color: #7c6cf4 !important; background: #0d0b1e !important; }

  .ext-check {
    flex-shrink: 0;
    width: 15px; height: 15px;
    margin-top: 2px;
    accent-color: #7c6cf4;
    cursor: pointer;
  }

  .ext-info {
    display: flex;
    flex-direction: column;
    gap: 3px;
    flex: 1;
    min-width: 0;
  }
  .ext-name {
    font-size: 13px;
    font-weight: 600;
    color: #ddd;
    font-family: 'SF Mono', 'Fira Code', monospace;
  }
  .ext-desc {
    font-size: 11px;
    color: #555;
    line-height: 1.4;
  }

  .ext-order-badge {
    flex-shrink: 0;
    font-size: 10px;
    font-weight: 700;
    background: #1e1b3a;
    color: #9d91f5;
    border: 1px solid #2e2b55;
    border-radius: 4px;
    padding: 2px 6px;
    align-self: center;
  }

  .ext-summary {
    margin: 0;
    font-size: 12px;
    color: #555;
    padding: 4px 0;
  }

  /* ── Step 1 index-type section ──────────────────────────────────────────────── */

  .index-type-section {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .index-type-desc {
    margin: 0;
    font-size: 11px;
    color: #555;
    line-height: 1.6;
    background: #0f0f0f;
    border: 1px solid #1e1e1e;
    border-radius: 7px;
    padding: 10px 12px;
  }
  .index-type-desc strong { color: #888; font-weight: 600; }
  .index-type-desc code {
    font-size: 10px;
    background: #1e1e1e;
    padding: 1px 4px;
    border-radius: 3px;
    color: #9d91f5;
  }

  .field-group-label {
    font-size: 11px;
    font-weight: 700;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: -6px;
  }

  .ext-info-box {
    background: #0f0f0f;
    border: 1px solid #1e1e1e;
    border-radius: 7px;
    padding: 10px 12px;
  }
  .ext-info-text {
    margin: 0;
    font-size: 11px;
    color: #555;
    line-height: 1.5;
  }
  .ext-info-text code {
    font-size: 10px;
    background: #1e1e1e;
    padding: 1px 4px;
    border-radius: 3px;
    color: #9d91f5;
  }

  /* ── Shared ────────────────────────────────────────────────────────────────── */

  .type-toggle {
    display: flex;
    border: 1px solid #2e2e2e;
    border-radius: 8px;
    overflow: hidden;
  }
  .type-toggle button {
    flex: 1; background: transparent; border: none; color: #666;
    padding: 8px; font-size: 13px; cursor: pointer; transition: background 0.15s;
  }
  .type-toggle button.sel { background: #7c6cf4; color: #fff; }

  .actions {
    display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px;
  }
  .btn-primary {
    background: #7c6cf4; color: #fff; border: none;
    border-radius: 7px; padding: 9px 18px; font-size: 13px; font-weight: 600; cursor: pointer;
  }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary {
    background: transparent; color: #888; border: 1px solid #333;
    border-radius: 7px; padding: 9px 18px; font-size: 13px; cursor: pointer;
  }
  .err { color: #e55; font-size: 12px; margin: 0; }
  .spinner {
    width: 36px; height: 36px;
    border: 3px solid #2a2a2a; border-top-color: #7c6cf4;
    border-radius: 50%; animation: spin 0.8s linear infinite; margin: 12px auto;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .status { font-size: 14px; color: #aaa; margin: 0; }
  .progress-bar {
    width: 100%; height: 4px; background: #222; border-radius: 2px; overflow: hidden; margin-top: 8px;
  }
  .fill { height: 100%; background: #7c6cf4; transition: width 0.4s; }

  /* ── Embedding model step ───────────────────────────────────────────────────── */

  .embed-model-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 340px;
    overflow-y: auto;
  }

  .embed-model-card {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    background: #0f0f0f;
    border: 1px solid #1e1e1e;
    border-radius: 8px;
    padding: 10px 12px;
    cursor: pointer;
    transition: border-color 0.15s, background 0.1s;
    /* override generic label flex-direction */
    flex-direction: row !important;
  }
  .embed-model-card:hover { background: #131313; border-color: #2e2e2e; }
  .embed-model-card-selected { border-color: #7c6cf4 !important; background: #0d0b1e !important; }

  .embed-radio {
    flex-shrink: 0;
    width: 15px; height: 15px;
    margin-top: 3px;
    accent-color: #7c6cf4;
    cursor: pointer;
  }

  .embed-model-info {
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex: 1;
    min-width: 0;
  }

  .embed-model-top {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
  }

  .embed-model-name {
    font-size: 13px;
    font-weight: 600;
    color: #ddd;
    font-family: 'SF Mono', 'Fira Code', monospace;
  }

  .embed-dim-badge {
    font-size: 10px;
    font-weight: 700;
    background: #1e1b3a;
    color: #9d91f5;
    border: 1px solid #2e2b55;
    border-radius: 4px;
    padding: 1px 6px;
  }

  .embed-provider-badge {
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

  .embed-active-badge {
    font-size: 10px;
    font-weight: 700;
    background: #0f2a1a;
    color: #5cdb85;
    border: 1px solid #1e3d29;
    border-radius: 4px;
    padding: 1px 6px;
  }

  .embed-model-desc {
    font-size: 11px;
    color: #555;
    line-height: 1.4;
  }

  .embed-dim-warn {
    background: #1a1000;
    border: 1px solid #4a3000;
    border-radius: 7px;
    padding: 10px 12px;
    font-size: 12px;
    color: #c8a040;
    line-height: 1.5;
  }
</style>
