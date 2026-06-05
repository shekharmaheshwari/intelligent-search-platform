<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { api } from '../lib/api';
  import type { RestConnectorConfig, SitemapConnectorConfig } from '../lib/types';

  const dispatch = createEventDispatcher<{ done: string; cancel: void }>();

  let step = 1;
  let busy = false;
  let errMsg = '';

  // Step 1
  let name = '';
  let description = '';
  let indexId = '';

  // Step 2 — connector type
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

  // Step 3 — field mapping
  let resultPath = '';
  let idField = '';
  let titleField = '';
  let contentField = '';
  let urlField = '';
  // Extra arbitrary source→target rows
  let extraMappings: Array<{ src: string; tgt: string }> = [];

  function addMapping() {
    extraMappings = [...extraMappings, { src: '', tgt: '' }];
  }
  function removeMapping(i: number) {
    extraMappings = extraMappings.filter((_, idx) => idx !== i);
  }

  const TARGET_SUGGESTIONS = ['title', 'content', 'url', 'id', 'source_type', 'category', 'price', 'manufacturer', 'brand', 'tags'];

  // Step 4 — progress
  let statusText = 'Queued...';
  let statusIndexed = 0;
  let statusTotal = 0;

  async function stepOne() {
    if (!name.trim()) { errMsg = 'Name is required'; return; }
    busy = true; errMsg = '';
    try {
      const meta = await api.createIndex(name.trim(), description.trim());
      indexId = meta.id;
      step = 2;
    } catch (e) {
      errMsg = String(e);
    } finally {
      busy = false;
    }
  }

  function stepTwo() {
    errMsg = '';
    if (connectorType === 'rest' && !restUrl.trim()) { errMsg = 'URL is required'; return; }
    if (connectorType === 'sitemap' && !sitemapUrl.trim()) { errMsg = 'Sitemap URL is required'; return; }
    step = 3;
  }

  async function stepThree() {
    busy = true; errMsg = '';
    try {
      // Build field_map from extra rows (skip incomplete rows)
      const field_map: Record<string, string> = {};
      for (const row of extraMappings) {
        if (row.src.trim()) field_map[row.src.trim()] = row.tgt.trim();
      }

      if (connectorType === 'rest') {
        let parsedHeaders: Record<string, string> = {};
        if (restHeaders.trim()) {
          try { parsedHeaders = JSON.parse(restHeaders); } catch { /* ignore */ }
        }
        const cfg: RestConnectorConfig & { result_path?: string; id_field?: string; title_field?: string; content_field?: string; url_field?: string; field_map?: Record<string,string> } = {
          url: restUrl.trim(),
          method: restMethod,
          headers: parsedHeaders,
          body: restBody.trim() || undefined,
          ...(resultPath.trim()   && { result_path:   resultPath.trim() }),
          ...(idField.trim()      && { id_field:       idField.trim() }),
          ...(titleField.trim()   && { title_field:    titleField.trim() }),
          ...(contentField.trim() && { content_field:  contentField.trim() }),
          ...(urlField.trim()     && { url_field:      urlField.trim() }),
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
      step = 4;
      pollUntilDone();
    } catch (e) {
      errMsg = String(e);
    } finally {
      busy = false;
    }
  }

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
    {#each [1,2,3,4] as s}
      <div class="step" class:active={step === s} class:done={step > s}>{s}</div>
    {/each}
  </div>

  <!-- Step 1: Name -->
  {#if step === 1}
    <div class="body">
      <label>Name <input bind:value={name} placeholder="My Knowledge Base" /></label>
      <label>Description <input bind:value={description} placeholder="Optional" /></label>
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
        <button class="btn-primary" on:click={stepThree} disabled={busy}>
          {busy ? 'Starting…' : 'Start Ingestion →'}
        </button>
      </div>
    </div>

  <!-- Step 4: Progress -->
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
</style>
