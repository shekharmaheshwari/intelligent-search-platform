<script lang="ts">
  import { onMount } from 'svelte';
  import { getApiBase } from '../sources/ApiPanel.helpers';

  const base = getApiBase();

  // ── State ──────────────────────────────────────────────────────────────────
  let indexName   = '';
  let selectedFile = '';
  let files: string[] = [];
  let filesLoading = true;
  let mode: 'auto' | 'hybrid' | 'ai' = 'auto';
  let apiKey      = '';
  let feedback    = '';

  type PhaseState = 'idle' | 'running' | 'done' | 'error';
  let phase: PhaseState = 'idle';

  interface LogEntry {
    step:    string;
    status:  'running' | 'ok' | 'error';
    message: string;
    extra?:  Record<string, unknown>;
  }
  let log: LogEntry[] = [];
  let resultIndexId  = '';
  let errorMessage   = '';
  let previewDoc: Record<string, unknown> | null = null;
  let previewDocOpen = true;

  // Shopify format: root key is always "products"
  const ROOT_KEY = 'products';

  onMount(async () => {
    try {
      const res = await fetch(`${base}/agent/data-files`);
      const data = await res.json();
      files = data.files || [];
      if (files.length === 1) selectedFile = files[0];
    } catch {
      files = [];
    } finally {
      filesLoading = false;
    }
  });

  function stepLabel(step: string): string {
    const map: Record<string, string> = {
      load:                'Read JSON file',
      schema:              'Extract schema',
      analyse:             'Detect fields',
      create_index:        'Create index',
      save_extensions:     'Save extensions',
      assign_extensions:   'Assign extensions',
      configure_connector: 'Configure connector',
      field_config:        'Apply field config',
      rebuild:             'Ingest & embed',
      done:                'Complete',
    };
    return map[step] || step;
  }

  $: jsonPath = selectedFile ? `/app/data/${selectedFile}` : '';
  $: canRun   = !!indexName.trim() && !!selectedFile;

  async function runSetup(previewOnly = false) {
    if (!canRun) return;
    phase = 'running';
    log   = [];
    resultIndexId = '';
    errorMessage  = '';

    const endpoint = previewOnly ? `${base}/agent/preview` : `${base}/agent/setup`;
    const body = {
      index_name: indexName,
      json_path:  jsonPath,
      root_key:   ROOT_KEY,
      api_key:    apiKey,
      feedback,
      mode,
    };

    if (previewOnly) {
      try {
        const res  = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Preview failed');
        log = [
          { step: 'load',    status: 'ok', message: `${data.total_items} items found` },
          { step: 'schema',  status: 'ok', message: `${Object.keys(data.schema || {}).length} fields detected` },
          { step: 'analyse', status: 'ok', message: `Analysis complete — ${(data.artifacts?.field_config || []).length} fields mapped` },
        ];
        previewDoc    = data.preview_doc || null;
        previewDocOpen = true;
        phase = 'done';
      } catch (e: any) {
        errorMessage = e.message;
        phase = 'error';
      }
      return;
    }

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => ({ detail: 'Request failed' }));
        throw new Error(err.detail || 'Request failed');
      }

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let   buffer  = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          let event: any;
          try { event = JSON.parse(raw); } catch { continue; }

          const existing = log.findIndex(e => e.step === event.step);
          const entry: LogEntry = { step: event.step, status: event.status,
                                    message: event.message, extra: event };
          if (existing >= 0) { log[existing] = entry; log = [...log]; }
          else               { log = [...log, entry]; }

          if (event.step === 'done' && event.status === 'ok') {
            resultIndexId = event.index_id || '';
            phase = 'done';
          }
          if (event.status === 'error') {
            errorMessage = event.message;
            phase = 'error';
          }
        }
      }
    } catch (e: any) {
      errorMessage = e.message;
      phase = 'error';
    }
  }

  function reset() {
    phase = 'idle'; log = []; resultIndexId = '';
    errorMessage = ''; feedback = ''; previewDoc = null;
  }
</script>

<div class="shopify-setup">
  <h3 class="section-title">Shopify Products</h3>
  <p class="section-desc">
    Select a Shopify product export file. Fields are detected automatically —
    no AI required for standard Shopify catalogs.
  </p>

  {#if phase === 'idle' || phase === 'error'}
    <form class="setup-form" on:submit|preventDefault={() => runSetup(false)}>

      <div class="field-row">
        <label class="field-label" for="sh-file">Product file</label>
        {#if filesLoading}
          <p class="hint">Loading files…</p>
        {:else if files.length === 0}
          <p class="hint warn">No .json files found in <code>/app/data/</code>. Drop your Shopify export there.</p>
        {:else}
          <select id="sh-file" class="field-input" bind:value={selectedFile} required>
            <option value="">— select a file —</option>
            {#each files as f}
              <option value={f}>{f}</option>
            {/each}
          </select>
          {#if selectedFile}
            <span class="path-hint">/app/data/{selectedFile} · root key: products</span>
          {/if}
        {/if}
      </div>

      <div class="field-row">
        <label class="field-label" for="sh-name">Index name</label>
        <input id="sh-name" class="field-input" type="text"
               placeholder="My Shopify Store"
               bind:value={indexName} required />
      </div>

      <div class="field-row">
        <label class="field-label">
          Analysis mode
          <span class="field-hint">Auto-detect is recommended for Shopify</span>
        </label>
        <div class="mode-group">
          <button type="button" class="mode-btn" class:mode-active={mode === 'auto'}
                  on:click={() => mode = 'auto'}>
            <span class="mode-icon">⚡</span>
            <span class="mode-name">Auto-detect</span>
            <span class="mode-desc">Code only · instant · free</span>
          </button>
          <button type="button" class="mode-btn" class:mode-active={mode === 'hybrid'}
                  on:click={() => mode = 'hybrid'}>
            <span class="mode-icon">⚖</span>
            <span class="mode-name">Hybrid</span>
            <span class="mode-desc">Code first · AI if needed</span>
          </button>
          <button type="button" class="mode-btn" class:mode-active={mode === 'ai'}
                  on:click={() => mode = 'ai'}>
            <span class="mode-icon">✦</span>
            <span class="mode-name">AI only</span>
            <span class="mode-desc">Claude · handles any structure</span>
          </button>
        </div>
      </div>

      {#if mode !== 'auto'}
        <div class="field-row">
          <label class="field-label" for="sh-key">
            Anthropic API key
            <span class="field-hint">leave blank to use key from Chat Model settings</span>
          </label>
          <input id="sh-key" class="field-input" type="password"
                 placeholder="sk-ant-..." bind:value={apiKey} />
        </div>
      {/if}

      {#if errorMessage}
        <p class="err">{errorMessage}</p>
      {/if}

      <div class="form-actions">
        <button type="button" class="btn-preview"
                on:click={() => runSetup(true)} disabled={!canRun}>
          Preview schema
        </button>
        <button type="submit" class="btn-build" disabled={!canRun}>
          Build Index
        </button>
      </div>
    </form>
  {/if}

  {#if phase === 'running' || phase === 'done' || (phase === 'error' && log.length > 0)}
    <div class="progress-panel">
      <div class="progress-header">
        <span class="progress-title">Building — {indexName}</span>
        {#if phase !== 'running'}
          <button class="btn-reset" on:click={reset}>← Start over</button>
        {/if}
      </div>

      <div class="log-list">
        {#each log as entry (entry.step)}
          <div class="log-row" class:log-ok={entry.status === 'ok'}
               class:log-err={entry.status === 'error'}
               class:log-running={entry.status === 'running'}>
            <span class="log-icon">
              {#if entry.status === 'ok'}✓{:else if entry.status === 'error'}✗{:else}◐{/if}
            </span>
            <span class="log-step">{stepLabel(entry.step)}</span>
            <span class="log-msg">{entry.message}</span>
          </div>
        {/each}
        {#if phase === 'running'}
          <div class="log-row log-running">
            <span class="log-icon spin">◌</span>
            <span class="log-msg">Working…</span>
          </div>
        {/if}
      </div>

      {#if phase === 'done' && resultIndexId}
        <div class="done-actions">
          <a class="done-btn" href="/?index={resultIndexId}">View Index →</a>
          <a class="done-btn" href="/?index={resultIndexId}&tab=search">Test Search →</a>
        </div>
      {/if}

      {#if phase === 'done' && !resultIndexId && previewDoc}
        <div class="preview-doc-block">
          <button class="preview-doc-toggle" on:click={() => previewDocOpen = !previewDocOpen}>
            <span>Normalized sample document</span>
            <span>{previewDocOpen ? '▲' : '▼'}</span>
          </button>
          {#if previewDocOpen}
            <pre class="preview-doc-json">{JSON.stringify(previewDoc, null, 2)}</pre>
          {/if}
        </div>
        <div class="confirm-actions">
          <p class="confirm-hint">Schema looks right? Build the index now.</p>
          <button class="btn-build confirm-btn" on:click={() => runSetup(false)}>
            Build Index →
          </button>
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .shopify-setup { max-width: 720px; }

  .section-title { font-size: 15px; font-weight: 600; color: var(--text-1); margin: 0 0 6px; }
  .section-desc  { font-size: 13px; color: var(--text-5); margin: 0 0 24px; line-height: 1.5; }

  .setup-form { display: flex; flex-direction: column; gap: 20px; }

  .field-row   { display: flex; flex-direction: column; gap: 6px; }
  .field-label { font-size: 13px; font-weight: 600; color: var(--text-2); display: flex; align-items: baseline; gap: 8px; }
  .field-hint  { font-size: 11px; font-weight: 400; color: var(--text-5); }
  .field-input { padding: 9px 12px; border: 1px solid var(--border); border-radius: 6px;
                 background: var(--surface); color: var(--text-1); font-size: 13px;
                 font-family: monospace; outline: none; }
  .field-input:focus { border-color: var(--accent); }

  .path-hint { font-size: 11px; color: var(--text-5); font-family: monospace; }

  .hint      { font-size: 12px; color: var(--text-5); margin: 0; }
  .hint.warn { color: var(--warn, #c97b00); }
  code       { font-family: monospace; }

  .mode-group { display: flex; gap: 8px; }
  .mode-btn {
    flex: 1; display: flex; flex-direction: column; align-items: center; gap: 3px;
    padding: 10px 8px; border: 1.5px solid var(--border); border-radius: 8px;
    background: var(--surface); cursor: pointer; transition: border-color 0.15s, background 0.15s;
    font-family: inherit;
  }
  .mode-btn:hover  { border-color: var(--accent); }
  .mode-active     { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, var(--surface)); }
  .mode-icon       { font-size: 16px; }
  .mode-name       { font-size: 12px; font-weight: 600; color: var(--text-2); }
  .mode-desc       { font-size: 10px; color: var(--text-5); text-align: center; }
  .mode-active .mode-name { color: var(--accent); }

  .err { font-size: 12px; color: var(--error, #c0392b); margin: 0; }

  .form-actions { display: flex; gap: 10px; padding-top: 4px; }
  .btn-preview  { padding: 9px 16px; border: 1px solid var(--border); border-radius: 6px;
                  background: none; color: var(--text-3); font-size: 13px; cursor: pointer; }
  .btn-preview:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-build    { padding: 9px 20px; border: none; border-radius: 6px;
                  background: var(--accent); color: #fff; font-size: 13px;
                  font-weight: 600; cursor: pointer; }
  .btn-build:disabled { opacity: 0.4; cursor: not-allowed; }

  .progress-panel  { margin-top: 20px; }
  .progress-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .progress-title  { font-size: 13px; font-weight: 600; color: var(--text-2); }
  .btn-reset       { font-size: 12px; color: var(--accent); background: none; border: none; cursor: pointer; }

  .log-list { display: flex; flex-direction: column; gap: 4px; }
  .log-row  { display: flex; align-items: baseline; gap: 10px; font-size: 12px;
               padding: 6px 10px; border-radius: 5px; background: var(--surface-2, #f8f8f8); }
  .log-ok      { color: var(--text-3); }
  .log-err     { color: var(--error, #c0392b); background: #fff0f0; }
  .log-running { color: var(--text-5); }
  .log-icon    { width: 14px; font-size: 11px; }
  .log-step    { font-weight: 600; min-width: 140px; }
  .log-msg     { color: var(--text-5); }

  @keyframes spin { to { transform: rotate(360deg); } }
  .spin { display: inline-block; animation: spin 1s linear infinite; }

  .done-actions { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
  .done-btn     { padding: 8px 16px; border: 1px solid var(--accent); border-radius: 6px;
                  color: var(--accent); font-size: 12px; font-weight: 600; text-decoration: none; }

  .preview-doc-block  { margin-top: 16px; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .preview-doc-toggle { display: flex; justify-content: space-between; width: 100%;
                        padding: 10px 14px; background: var(--surface-2, #f5f5f5);
                        border: none; cursor: pointer; font-size: 12px; font-weight: 600;
                        color: var(--text-2); }
  .preview-doc-json   { margin: 0; padding: 14px; font-size: 11px; overflow-x: auto;
                        max-height: 340px; background: var(--surface); }

  .confirm-actions { display: flex; align-items: center; gap: 16px; margin-top: 16px;
                     padding: 14px 16px; background: color-mix(in srgb, var(--accent) 6%, var(--surface));
                     border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent);
                     border-radius: 8px; }
  .confirm-hint    { flex: 1; font-size: 13px; color: var(--text-3); margin: 0; }
  .confirm-btn     { white-space: nowrap; padding: 9px 20px; }
</style>
