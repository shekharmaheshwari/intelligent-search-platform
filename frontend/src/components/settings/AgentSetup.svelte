<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  import { getApiBase } from '../sources/ApiPanel.helpers';

  // ── State ──────────────────────────────────────────────────────────────────
  let indexName  = '';
  let jsonPath   = '';
  let rootKey    = '';
  let apiKey     = '';
  let feedback   = '';
  let mode: 'auto' | 'hybrid' | 'ai' = 'hybrid';

  type PhaseState = 'idle' | 'running' | 'done' | 'error';
  let phase: PhaseState = 'idle';

  interface LogEntry {
    step:    string;
    status:  'running' | 'ok' | 'error';
    message: string;
    extra?:  Record<string, unknown>;
  }
  let log: LogEntry[] = [];
  let resultIndexId   = '';
  let errorMessage    = '';
  let previewDoc: Record<string, unknown> | null = null;
  let previewDocOpen  = true;

  // ── Helpers ────────────────────────────────────────────────────────────────
  const base = getApiBase();

  function stepLabel(step: string): string {
    const map: Record<string, string> = {
      load:                 'Read JSON file',
      schema:               'Extract schema',
      analyse:              'Claude analysis',
      create_index:         'Create index',
      save_extensions:      'Save extensions',
      assign_extensions:    'Assign extensions',
      configure_connector:  'Configure connector',
      field_config:         'Apply field config',
      rebuild:              'Ingest & embed',
      done:                 'Complete',
    };
    return map[step] || step;
  }

  // ── Actions ────────────────────────────────────────────────────────────────
  async function runSetup(previewOnly = false) {
    if (!indexName.trim() || !jsonPath.trim()) return;

    phase = 'running';
    log   = [];
    resultIndexId = '';
    errorMessage  = '';

    const endpoint = previewOnly ? `${base}/agent/preview` : `${base}/agent/setup`;

    if (previewOnly) {
      // Preview returns plain JSON
      try {
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ index_name: indexName, json_path: jsonPath,
                                 root_key: rootKey, api_key: apiKey, feedback, mode }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Preview failed');
        log = [
          { step: 'load',    status: 'ok', message: `${data.total_items} items found`, extra: { root_key: data.root_key } },
          { step: 'schema',  status: 'ok', message: `${Object.keys(data.schema || {}).length} fields detected` },
          { step: 'analyse', status: 'ok', message: `Analysis complete — ${Object.keys(data.artifacts?.field_config || []).length} fields mapped, 3 extensions generated` },
        ];
        previewDoc = data.preview_doc || null;
        previewDocOpen = true;
        phase = 'done';
      } catch (e: any) {
        errorMessage = e.message;
        phase = 'error';
      }
      return;
    }

    // Full setup via SSE
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index_name: indexName, json_path: jsonPath,
                               root_key: rootKey, api_key: apiKey, feedback, mode }),
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

          // Update or append log entry for this step
          const existing = log.findIndex(e => e.step === event.step);
          const entry: LogEntry = {
            step:    event.step,
            status:  event.status,
            message: event.message,
            extra:   event,
          };
          if (existing >= 0) {
            log[existing] = entry;
            log = [...log];
          } else {
            log = [...log, entry];
          }

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
    phase         = 'idle';
    log           = [];
    resultIndexId = '';
    errorMessage  = '';
    feedback      = '';
    previewDoc    = null;
  }
</script>

<div class="agent-setup">
  <h3 class="section-title">Setup Agent</h3>
  <p class="section-desc">
    Provide a JSON data file and Claude will analyse its structure, generate all
    required extensions, configure the field schema, and build a fully searchable
    index — automatically.
  </p>

  {#if phase === 'idle' || phase === 'error'}
    <form class="setup-form" on:submit|preventDefault={() => runSetup(false)}>

      <div class="field-row">
        <label class="field-label" for="ag-name">Index name</label>
        <input id="ag-name" class="field-input" type="text"
               placeholder="My Product Catalog"
               bind:value={indexName} required />
      </div>

      <div class="field-row">
        <label class="field-label" for="ag-path">
          JSON data path
          <span class="field-hint">local file path or https:// URL</span>
        </label>
        <input id="ag-path" class="field-input" type="text"
               placeholder="/data/products.json"
               bind:value={jsonPath} required />
      </div>

      <div class="field-row">
        <label class="field-label" for="ag-root">
          Root key
          <span class="field-hint">optional — auto-detected if empty</span>
        </label>
        <input id="ag-root" class="field-input" type="text"
               placeholder="catalog"
               bind:value={rootKey} />
      </div>

      <div class="field-row">
        <label class="field-label">
          Analysis mode
          <span class="field-hint">how fields and extensions are generated</span>
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
        <label class="field-label" for="ag-key">
          Anthropic API key
          <span class="field-hint">leave blank to use key from Chat Model settings</span>
        </label>
        <input id="ag-key" class="field-input" type="password"
               placeholder="sk-ant-..."
               bind:value={apiKey} />
      </div>
      {/if}

      {#if errorMessage}
        <p class="err">{errorMessage}</p>
      {/if}

      <div class="form-actions">
        <button type="button" class="btn-preview"
                on:click={() => runSetup(true)}
                disabled={!indexName.trim() || !jsonPath.trim()}>
          Preview schema
        </button>
        <button type="submit" class="btn-build"
                disabled={!indexName.trim() || !jsonPath.trim()}>
          Analyse &amp; Build Index
        </button>
      </div>
    </form>
  {/if}

  {#if phase === 'running' || phase === 'done' || (phase === 'error' && log.length > 0)}
    <div class="progress-panel">
      <div class="progress-header">
        <span class="progress-title">Agent Progress — {indexName}</span>
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
          <a class="done-btn" href="/indexes/{resultIndexId}">View Index →</a>
          <a class="done-btn" href="/indexes/{resultIndexId}?tab=search">Test Search →</a>
          <a class="done-btn" href="/indexes/{resultIndexId}?tab=extensions">View Extensions →</a>
        </div>
      {/if}

      {#if phase === 'done' && !resultIndexId}
        <!-- Preview doc — show one normalized sample document -->
        {#if previewDoc}
          <div class="preview-doc-block">
            <button class="preview-doc-toggle" on:click={() => previewDocOpen = !previewDocOpen}>
              <span>Normalized sample document</span>
              <span class="toggle-arrow">{previewDocOpen ? '▲' : '▼'}</span>
            </button>
            {#if previewDocOpen}
              <pre class="preview-doc-json">{JSON.stringify(previewDoc, null, 2)}</pre>
            {/if}
          </div>
        {/if}

        <!-- Feedback + confirm -->
        <div class="feedback-block">
          <label class="feedback-label" for="ag-feedback">
            Anything to fix?
            <span class="field-hint">Describe issues — e.g. "tags show raw key-value pairs", "variants show [object Object]"</span>
          </label>
          <textarea id="ag-feedback" class="feedback-input"
            placeholder="Leave blank and click 'Build Index' if everything looks correct..."
            bind:value={feedback}
            rows="3"
          ></textarea>
          <div class="feedback-actions">
            <button class="btn-preview" on:click={() => { phase = 'idle'; log = []; runSetup(true); }}
                    disabled={!feedback.trim()}>
              Re-analyse with feedback
            </button>
            <button class="btn-build" on:click={() => { phase = 'idle'; log = []; runSetup(false); }}>
              Looks good — Build Index
            </button>
          </div>
        </div>
      {/if}

      {#if phase === 'error'}
        <p class="err" style="margin-top:12px">{errorMessage}</p>
      {/if}
    </div>
  {/if}
</div>

<style>
  .agent-setup { display: flex; flex-direction: column; gap: 20px; }

  .section-title { font-size: 14px; font-weight: 600; color: var(--text-2); margin: 0 0 6px; }
  .section-desc  { font-size: 13px; color: var(--text-5); line-height: 1.6; margin: 0; }

  .setup-form { display: flex; flex-direction: column; gap: 16px; margin-top: 4px; }

  .field-row   { display: flex; flex-direction: column; gap: 5px; }
  .field-label { font-size: 12px; font-weight: 600; color: var(--text-3); display: flex; align-items: center; gap: 8px; }
  .field-hint  { font-size: 11px; font-weight: 400; color: var(--text-5); }
  .field-input {
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px 11px;
    font-size: 13px; color: var(--text-1);
    font-family: 'SF Mono', 'Fira Code', monospace;
    outline: none; transition: border-color 0.15s;
  }
  .field-input:focus { border-color: var(--accent); }

  .form-actions { display: flex; gap: 10px; margin-top: 4px; }

  .btn-preview {
    background: var(--surface); border: 1px solid var(--border-2);
    color: var(--text-3); border-radius: 7px; padding: 8px 16px;
    font-size: 13px; cursor: pointer; transition: border-color 0.15s, color 0.15s;
  }
  .btn-preview:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .btn-preview:disabled { opacity: 0.4; cursor: not-allowed; }

  .btn-build {
    background: var(--accent); border: none;
    color: #fff; border-radius: 7px; padding: 8px 20px;
    font-size: 13px; font-weight: 600; cursor: pointer;
    transition: opacity 0.15s;
  }
  .btn-build:hover:not(:disabled) { opacity: 0.85; }
  .btn-build:disabled { opacity: 0.4; cursor: not-allowed; }

  /* Progress panel */
  .progress-panel {
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px;
  }
  .progress-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 14px;
  }
  .progress-title { font-size: 12px; font-weight: 600; color: var(--text-3); }

  .btn-reset {
    background: none; border: none; color: var(--accent);
    font-size: 12px; cursor: pointer; padding: 0;
  }
  .btn-reset:hover { text-decoration: underline; }

  .log-list { display: flex; flex-direction: column; gap: 6px; }

  .log-row {
    display: grid;
    grid-template-columns: 18px 160px 1fr;
    align-items: baseline;
    gap: 8px;
    font-size: 12px;
    padding: 2px 0;
  }
  .log-ok      { color: var(--text-4); }
  .log-running { color: var(--text-3); }
  .log-err     { color: #e85454; }

  .log-icon { font-size: 13px; }
  .log-ok .log-icon    { color: #4ade80; }
  .log-err .log-icon   { color: #e85454; }
  .log-running .log-icon { color: var(--accent); }

  .log-step { font-weight: 600; }
  .log-msg  { color: var(--text-5); }

  @keyframes spin { to { transform: rotate(360deg); } }
  .spin { display: inline-block; animation: spin 1s linear infinite; }

  .done-actions {
    display: flex; gap: 10px; margin-top: 16px;
    padding-top: 14px; border-top: 1px solid var(--border-2);
  }
  .done-btn {
    background: var(--surface); border: 1px solid var(--border-2);
    color: var(--accent); border-radius: 6px; padding: 6px 14px;
    font-size: 12px; text-decoration: none; transition: border-color 0.15s;
  }
  .done-btn:hover { border-color: var(--accent); }

  .err { font-size: 12px; color: #e85454; margin: 0; }

  .preview-doc-block {
    margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border-2);
  }
  .preview-doc-toggle {
    display: flex; justify-content: space-between; align-items: center; width: 100%;
    background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
    padding: 8px 12px; font-size: 12px; font-weight: 600; color: var(--text-3);
    cursor: pointer; text-align: left;
  }
  .toggle-arrow { font-size: 10px; color: var(--text-5); }
  .preview-doc-json {
    margin: 6px 0 0; background: var(--bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 12px; font-size: 11px; font-family: 'SF Mono', 'Fira Code', monospace;
    color: var(--text-2); overflow-x: auto; max-height: 320px; overflow-y: auto;
    white-space: pre; line-height: 1.5;
  }

  .feedback-block {
    display: flex; flex-direction: column; gap: 10px;
    margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--border-2);
  }
  .feedback-label {
    font-size: 12px; font-weight: 600; color: var(--text-3);
    display: flex; flex-direction: column; gap: 3px;
  }
  .feedback-input {
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px 11px;
    font-size: 12px; color: var(--text-1); resize: vertical;
    font-family: inherit; outline: none; transition: border-color 0.15s;
  }
  .feedback-input:focus { border-color: var(--accent); }
  .feedback-actions { display: flex; gap: 10px; }
  .mode-group {
    display: flex;
    gap: 8px;
  }

  .mode-btn {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    padding: 10px 8px;
    border: 1.5px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
    font-family: inherit;
  }

  .mode-btn:hover {
    border-color: var(--accent);
  }

  .mode-active {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 8%, var(--surface));
  }

  .mode-icon {
    font-size: 16px;
  }

  .mode-name {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-2);
  }

  .mode-desc {
    font-size: 10px;
    color: var(--text-5);
    text-align: center;
  }

  .mode-active .mode-name {
    color: var(--accent);
  }
</style>
