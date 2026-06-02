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

  // Step 2
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

  async function stepTwo() {
    busy = true; errMsg = '';
    try {
      if (connectorType === 'rest') {
        if (!restUrl.trim()) { errMsg = 'URL is required'; busy = false; return; }
        let parsedHeaders: Record<string, string> = {};
        if (restHeaders.trim()) {
          try { parsedHeaders = JSON.parse(restHeaders); } catch { /* ignore */ }
        }
        const cfg: RestConnectorConfig = {
          url: restUrl.trim(),
          method: restMethod,
          headers: parsedHeaders,
          body: restBody.trim() || undefined,
        };
        await api.ingestRest(indexId, cfg);
      } else {
        if (!sitemapUrl.trim()) { errMsg = 'Sitemap URL is required'; busy = false; return; }
        const cfg: SitemapConnectorConfig = {
          url: sitemapUrl.trim(),
          max_pages: sitemapMaxPages,
          content_selector: sitemapSelector.trim(),
          include_pattern: sitemapInclude.trim(),
          exclude_pattern: sitemapExclude.trim(),
        };
        await api.ingestSitemap(indexId, cfg);
      }
      step = 3;
      pollUntilDone();
    } catch (e) {
      errMsg = String(e);
    } finally {
      busy = false;
    }
  }

  let statusText = 'Queued...';
  let statusIndexed = 0;
  let statusTotal = 0;

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
    {#each [1,2,3] as s}
      <div class="step" class:active={step === s} class:done={step > s}>{s}</div>
    {/each}
  </div>

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
        <button class="btn-primary" on:click={stepTwo} disabled={busy}>
          {busy ? 'Starting…' : 'Start Ingestion →'}
        </button>
      </div>
    </div>

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
  .step::before { content: ''; display: block; }
  .step.active { background: #7c6cf4; color: #fff; }
  .step.done { background: #2d5a3d; color: #5cdb85; }
  .body { padding: 0 24px 24px; display: flex; flex-direction: column; gap: 14px; }
  .body.center { align-items: center; text-align: center; padding-top: 12px; }
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
    outline: none;
    border-color: #7c6cf4;
  }
  .hint { font-size: 11px; color: #555; font-weight: 400; }
  .type-toggle {
    display: flex;
    border: 1px solid #2e2e2e;
    border-radius: 8px;
    overflow: hidden;
  }
  .type-toggle button {
    flex: 1;
    background: transparent;
    border: none;
    color: #666;
    padding: 8px;
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s;
  }
  .type-toggle button.sel {
    background: #7c6cf4;
    color: #fff;
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
    margin-top: 4px;
  }
  .btn-primary {
    background: #7c6cf4;
    color: #fff;
    border: none;
    border-radius: 7px;
    padding: 9px 18px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary {
    background: transparent;
    color: #888;
    border: 1px solid #333;
    border-radius: 7px;
    padding: 9px 18px;
    font-size: 13px;
    cursor: pointer;
  }
  .err { color: #e55; font-size: 12px; margin: 0; }
  .spinner {
    width: 36px; height: 36px;
    border: 3px solid #2a2a2a;
    border-top-color: #7c6cf4;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin: 12px auto;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .status { font-size: 14px; color: #aaa; margin: 0; }
  .progress-bar {
    width: 100%; height: 4px;
    background: #222;
    border-radius: 2px;
    overflow: hidden;
    margin-top: 8px;
  }
  .fill { height: 100%; background: #7c6cf4; transition: width 0.4s; }
</style>
