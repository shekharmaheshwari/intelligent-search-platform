<script lang="ts">
  /**
   * RebuildPanel — full-rebuild dialog for an ISP-managed index.
   *
   * Opened from the "↺ Rebuild" button in SourceContent.  Allows operators to:
   * - Paste a curl command to auto-fill all fields.
   * - Manually configure method, URL, headers, and request body.
   * - Trigger a rebuild that drops all existing documents and re-fetches from the connector.
   * - Track job progress in real time via the status-poll loop.
   *
   * Dispatches ``done`` when the rebuild completes and ``close`` when dismissed.
   */
  import { createEventDispatcher } from 'svelte';
  import { indexService } from '../../lib/services/indexes/IndexService';
  import type { IndexMeta, IngestStatus, RestConnectorConfig, SitemapConnectorConfig, GSheetConnectorConfig } from '../../lib/types';
  import { getExistingRest, getExistingSitemap, getExistingGsheet, parseCurl as parseCurlHelper, buildCurl } from './RebuildPanel.helpers';

  export let index: IndexMeta;

  const dispatch = createEventDispatcher<{ done: void; close: void }>();

  const connectorType = index.connectors[0]?.type ?? 'rest';

  const pre = getExistingRest(index);

  let url            = pre.url ?? '';
  let method         = pre.method ?? 'GET';
  let headersRaw     = pre.headers ? JSON.stringify(pre.headers, null, 2) : '{}';
  let body           = pre.body ?? '';

  // ── GSheet fields ─────────────────────────────────────────────────────────────
  const preGsheet = getExistingGsheet(index);
  let gsheetSpreadsheetId = preGsheet.spreadsheet_id ?? '';
  let gsheetSheetName     = preGsheet.sheet_name ?? 'Sheet1';
  let gsheetHeaderRow     = preGsheet.header_row ?? 1;
  let gsheetIdCol         = preGsheet.id_column ?? '';
  let gsheetTitleCol      = preGsheet.title_column ?? '';
  let gsheetContentCol    = preGsheet.content_column ?? '';
  let gsheetUrlCol        = preGsheet.url_column ?? '';
  let gsheetSkipEmpty     = preGsheet.skip_empty_rows ?? true;
  let gsheetMappings      = Object.entries(preGsheet.field_map ?? {}).map(([key, value]) => ({ key, value }));

  function addGsheetMapping()           { gsheetMappings = [...gsheetMappings, { key: '', value: '' }]; }
  function removeGsheetMapping(i: number) { gsheetMappings = gsheetMappings.filter((_, idx) => idx !== i); }

  // ── Sitemap fields ────────────────────────────────────────────────────────────
  const preSitemap = getExistingSitemap(index);
  let sitemapUrl       = preSitemap.url ?? '';
  let sitemapMaxPages  = preSitemap.max_pages ?? 50;
  let sitemapSelector  = preSitemap.content_selector ?? '';
  let sitemapUserAgent = preSitemap.user_agent ?? '';
  let includePatterns  = (preSitemap.include_patterns ?? []).map(v => ({ value: v }));
  let excludePatterns  = (preSitemap.exclude_patterns ?? []).map(v => ({ value: v }));
  let metaFields       = Object.entries(preSitemap.meta_fields ?? {}).map(([key, value]) => ({ key, value }));

  function addIncludePattern() { includePatterns = [...includePatterns, { value: '' }]; }
  function removeIncludePattern(i: number) { includePatterns = includePatterns.filter((_, idx) => idx !== i); }
  function addExcludePattern() { excludePatterns = [...excludePatterns, { value: '' }]; }
  function removeExcludePattern(i: number) { excludePatterns = excludePatterns.filter((_, idx) => idx !== i); }
  function addMetaField() { metaFields = [...metaFields, { key: '', value: '' }]; }
  function removeMetaField(i: number) { metaFields = metaFields.filter((_, idx) => idx !== i); }

  // ── Curl paste parser ─────────────────────────────────────────────────────
  const curlPlaceholder = `curl -X POST 'https://api.example.com/items' -H 'Authorization: Bearer TOKEN' -d '{"key": "value"}'`;
  let curlInput = '';
  let parseError = '';

  function parseCurl(raw: string) {
    const result = parseCurlHelper(raw);
    if (result.error) { parseError = result.error; return; }
    parseError = '';
    method     = result.method;
    url        = result.url;
    headersRaw = result.headersRaw;
    body       = result.body;
    curlInput  = '';
  }

  /** Intercept paste events in the curl textarea — if the pasted text is a curl command, parse it immediately and suppress the default paste. */
  function onCurlPaste(e: ClipboardEvent) {
    const text = e.clipboardData?.getData('text') ?? '';
    if (text.trim().startsWith('curl')) {
      e.preventDefault();
      parseCurl(text);
    }
  }

  // Progress state — lives alongside the form
  type RunState = 'idle' | 'running' | 'done' | 'error';
  let runState: RunState = 'idle';
  let stage = '';          // "Fetching…" | "Embedding…" | "Indexing…"
  let statusIndexed = 0;
  let statusTotal = 0;
  let errMsg = '';
  let copied = false;

  $: pct = statusTotal > 0 ? Math.round(statusIndexed / statusTotal * 100) : 0;

  $: curlCmd = buildCurl(url, method, headersRaw, body);

  /** Copy the current curl preview to the clipboard and show a brief "Copied" confirmation. */
  function copyCurl() {
    navigator.clipboard.writeText(curlCmd);
    copied = true;
    setTimeout(() => { copied = false; }, 1500);
  }

  /** Validate the form, build the correct connector config, call rebuild, then start polling. */
  async function runRebuild() {
    errMsg = '';
    runState = 'running';
    stage = 'Fetching content…';
    statusIndexed = 0; statusTotal = 0;

    try {
      if (connectorType === 'gsheet') {
        if (!gsheetSpreadsheetId.trim()) { errMsg = 'Spreadsheet ID is required'; runState = 'idle'; return; }
        if (!gsheetTitleCol.trim()) { errMsg = 'Title column is required — documents with no title are dropped'; runState = 'idle'; return; }
        const cfg: GSheetConnectorConfig = {
          type: 'gsheet',
          spreadsheet_id: gsheetSpreadsheetId.trim(),
          sheet_name:     gsheetSheetName.trim() || 'Sheet1',
          header_row:     gsheetHeaderRow,
          id_column:      gsheetIdCol.trim(),
          title_column:   gsheetTitleCol.trim(),
          content_column: gsheetContentCol.trim(),
          url_column:     gsheetUrlCol.trim(),
          skip_empty_rows: gsheetSkipEmpty,
          field_map: Object.fromEntries(gsheetMappings.filter(r => r.key.trim() && r.value.trim()).map(r => [r.key.trim(), r.value.trim()])),
        };
        await indexService.rebuild(index.id, cfg);
      } else if (connectorType === 'sitemap') {
        if (!sitemapUrl.trim()) { errMsg = 'Sitemap URL is required'; runState = 'idle'; return; }
        const cfg: SitemapConnectorConfig = {
          url: sitemapUrl.trim(),
          max_pages: sitemapMaxPages,
          content_selector: sitemapSelector.trim(),
          user_agent: sitemapUserAgent.trim(),
          include_patterns: includePatterns.map(r => r.value.trim()).filter(Boolean),
          exclude_patterns: excludePatterns.map(r => r.value.trim()).filter(Boolean),
          meta_fields: Object.fromEntries(metaFields.filter(r => r.key.trim() && r.value.trim()).map(r => [r.key.trim(), r.value.trim()])),
        };
        await indexService.rebuild(index.id, cfg);
      } else {
        if (!url.trim()) { errMsg = 'URL is required'; runState = 'idle'; return; }
        let headers: Record<string, string> = {};
        try { headers = JSON.parse(headersRaw || '{}'); } catch { errMsg = 'Invalid JSON in headers'; runState = 'idle'; return; }
        const cfg: RestConnectorConfig = {
          url: url.trim(), method, headers, body: body.trim(),
        };
        await indexService.rebuild(index.id, cfg);
      }
      poll();
    } catch (e) {
      runState = 'error';
      const msg = String(e);
      // 409 means a job is already active (same-instance fast-path check)
      errMsg = msg.includes('409')
        ? 'A rebuild is already in progress. Wait for it to finish.'
        : msg;
    }
  }

  /**
   * Poll the backend job status every 1.2 s until the job reaches a terminal state.
   * Updates ``stage``, ``statusIndexed``, and ``statusTotal`` to drive the progress UI.
   * Dispatches ``done`` on success; sets ``runState = 'error'`` on failure.
   */
  async function poll() {
    while (true) {
      await new Promise(r => setTimeout(r, 1200));
      try {
        const s: IngestStatus = await indexService.getStatus(index.id);
        statusIndexed = s.indexed;
        statusTotal   = s.total;

        if (s.state === 'queued') {
          stage = 'Queued…';
        } else if (s.state === 'running') {
          if (!s.total) {
            stage = 'Fetching content…';
          } else if (s.indexed < s.total * 0.5) {
            stage = 'Generating embeddings…';
          } else {
            stage = 'Indexing into Typesense…';
          }
        } else if (s.state === 'done') {
          statusIndexed = s.indexed;
          statusTotal   = s.indexed;
          stage = `Complete`;
          runState = 'done';
          dispatch('done');
          break;
        } else if (s.state === 'error') {
          runState = 'error';
          errMsg = s.error || 'Unknown error';
          break;
        }
      } catch { /* ignore transient */ }
    }
  }

  /** Reset all progress state so the form can be submitted again. */
  function reset() {
    runState = 'idle';
    errMsg = '';
    stage = '';
    statusIndexed = 0;
    statusTotal = 0;
  }
</script>

<div class="panel">
  <div class="panel-head">
    <div class="panel-title">
      <span class="icon">↺</span>
      Rebuild — {index.name}
    </div>
    <button class="close" on:click={() => dispatch('close')}>✕</button>
  </div>

  <div class="body">
    {#if connectorType === 'gsheet'}
      <!-- GSheet form -->
      <fieldset class="form" disabled={runState === 'running'}>
        <label class="field">
          Spreadsheet ID <span class="req">*</span>
          <input bind:value={gsheetSpreadsheetId} placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms" />
        </label>
        <div class="field-row">
          <label class="field" style="flex:2">
            Sheet name <span class="hint-inline">(tab name, default Sheet1)</span>
            <input bind:value={gsheetSheetName} placeholder="Sheet1" />
          </label>
          <label class="field" style="flex:1">
            Header row
            <input type="number" bind:value={gsheetHeaderRow} min="1" />
          </label>
        </div>
        <div class="section-label">Column mapping</div>
        <div class="grid2">
          <label class="field">Title column <span class="req">*</span><input bind:value={gsheetTitleCol} placeholder="Title" /></label>
          <label class="field">URL column<input bind:value={gsheetUrlCol} placeholder="url" /></label>
          <label class="field">Content column<input bind:value={gsheetContentCol} placeholder="description" /></label>
          <label class="field">ID column <span class="hint-inline">(auto if blank)</span><input bind:value={gsheetIdCol} placeholder="" /></label>
        </div>

        <div class="field">
          <span class="field-label-text">Extra field renames <span class="hint-inline">(sheet column → ISP field)</span></span>
          {#each gsheetMappings as row, i}
            <div class="extra-row">
              <input bind:value={row.key} placeholder="Sheet column" class="field-input" />
              <span class="arrow-sep">→</span>
              <input bind:value={row.value} placeholder="ISP field" class="field-input" />
              <button class="rm-btn" type="button" on:click={() => removeGsheetMapping(i)}>✕</button>
            </div>
          {/each}
          <button type="button" class="add-mapping-btn" on:click={addGsheetMapping}>+ Add rename</button>
        </div>

        <label class="field" style="flex-direction:row;align-items:center;gap:8px">
          <input type="checkbox" bind:checked={gsheetSkipEmpty} />
          <span>Skip empty rows</span>
        </label>
      </fieldset>

    {:else if connectorType !== 'sitemap'}
      <!-- Paste curl shortcut — REST only -->
      <div class="paste-curl-wrap">
        <div class="paste-curl-label">
          <span class="paste-icon">⎘</span>
          Paste a curl command to auto-fill all fields
        </div>
        <textarea
          class="paste-curl-input"
          bind:value={curlInput}
          on:paste={onCurlPaste}
          on:input={() => { if (curlInput.trim()) parseCurl(curlInput); }}
          placeholder={curlPlaceholder}
          rows="2"
          spellcheck="false"
          disabled={runState === 'running'}
        ></textarea>
        {#if parseError}<p class="parse-err">{parseError}</p>{/if}
      </div>

      <div class="divider"><span>or fill manually</span></div>

      <!-- REST form — disabled while running -->
      <fieldset class="form" disabled={runState === 'running'}>
        <div class="field-row">
          <label class="field" style="flex:0 0 90px">
            Method
            <select bind:value={method}>
              <option>GET</option><option>POST</option><option>PUT</option>
            </select>
          </label>
          <label class="field" style="flex:1">
            Endpoint URL <span class="req">*</span>
            <input bind:value={url} placeholder="https://api.example.com/data" />
          </label>
        </div>

        <label class="field">
          Headers <span class="hint-inline">(JSON)</span>
          <textarea bind:value={headersRaw} rows="3" spellcheck="false"
            placeholder={'{\n  "Authorization": "Bearer TOKEN"\n}'} />
        </label>

        <label class="field">
          Request body <span class="hint-inline">(JSON — sent for POST/PUT)</span>
          <textarea bind:value={body} rows="3" spellcheck="false"
            placeholder={'{\n  "familyCategory": "CELLPHONES",\n  "pageNumber": "1"\n}'} />
        </label>

      </fieldset>

      <!-- Curl preview -->
      <div class="curl-block">
        <div class="curl-head">
          <span>curl preview</span>
          <button class="copy-btn" on:click={copyCurl}>{copied ? '✓ Copied' : 'Copy'}</button>
        </div>
        <pre class="curl-pre"><code>{curlCmd}</code></pre>
      </div>
    {:else}
      <!-- Sitemap form -->
      <fieldset class="form" disabled={runState === 'running'}>
        <label class="field">
          Sitemap URL <span class="req">*</span>
          <input bind:value={sitemapUrl} placeholder="https://example.com/sitemap.xml" />
        </label>
        <div class="field-row">
          <label class="field" style="flex:1">
            Max pages <span class="hint-inline">(optional, default 50)</span>
            <input type="number" bind:value={sitemapMaxPages} min="1" max="10000" />
          </label>
          <label class="field" style="flex:1">
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
              <span class="arrow-sep">→</span>
              <input bind:value={row.value} placeholder="site_name" class="field-input" />
              <button class="rm-btn" type="button" on:click={() => removeMetaField(i)}>✕</button>
            </div>
          {/each}
          <button type="button" class="add-mapping-btn" on:click={addMetaField}>+ Add field</button>
        </div>
      </fieldset>
    {/if}


    {#if errMsg && runState !== 'running'}
      <p class="err">{errMsg}</p>
    {/if}

    <!-- Actions + inline progress -->
    <div class="footer">
      {#if runState === 'idle' || runState === 'error'}
        <div class="actions">
          <button class="btn-cancel" on:click={() => dispatch('close')}>Cancel</button>
          {#if connectorType !== 'sitemap' && connectorType !== 'gsheet'}
            <button class="btn-copy-curl" on:click={copyCurl} disabled={!url.trim()} title="Copy full curl command">
              {copied ? '✓ Copied' : '⎘ Copy curl'}
            </button>
          {/if}
          <button class="btn-rebuild" on:click={runRebuild}
            disabled={connectorType === 'sitemap' ? !sitemapUrl.trim() : connectorType === 'gsheet' ? !gsheetSpreadsheetId.trim() : !url.trim()}>
            ↺ Run Rebuild
          </button>
        </div>

      {:else if runState === 'running'}
        <div class="progress-section">
          <div class="progress-header">
            <div class="stage-row">
              <div class="pulse-dot"></div>
              <span class="stage-text">{stage}</span>
            </div>
            <span class="pct-label">{pct}%</span>
          </div>
          <div class="progress-bar">
            <div class="fill" style="width:{pct}%"></div>
          </div>
          {#if statusTotal > 0}
            <div class="doc-counter">
              <span class="doc-done">{statusIndexed.toLocaleString()}</span>
              <span class="doc-sep">/</span>
              <span class="doc-total">{statusTotal.toLocaleString()} documents</span>
            </div>
          {:else}
            <div class="doc-counter"><span class="doc-total">Starting…</span></div>
          {/if}
        </div>

      {:else if runState === 'done'}
        <div class="done-section">
          <div class="done-row">
            {#if statusIndexed === 0}
              <span class="warn-check">⚠</span>
              <span class="done-text warn-text">
                Completed but <strong>0 documents</strong> indexed.<br>
                Set <strong>Result path</strong> and field mapping correctly, then retry.
              </span>
            {:else}
              <span class="done-check">✓</span>
              <span class="done-text">
                Rebuilt — <strong>{statusIndexed.toLocaleString()} documents</strong> indexed
              </span>
            {/if}
          </div>
          <button class="btn-cancel" on:click={() => { reset(); dispatch('close'); }}>Close</button>
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .panel {
    background: var(--bg-3);
    border: 1px solid var(--border-2);
    border-radius: 12px;
    width: 560px;
    max-width: 96vw;
    max-height: 92vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .panel-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .panel-title {
    display: flex; align-items: center; gap: 8px;
    font-size: 14px; font-weight: 600; color: var(--text);
  }
  .icon { color: var(--accent); font-size: 16px; }
  .close { background: none; border: none; color: var(--text-5); cursor: pointer; font-size: 14px; }
  .close:hover { color: var(--text-3); }

  .body {
    padding: 20px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .paste-curl-wrap {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .paste-curl-label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 600;
    color: var(--accent);
  }
  .paste-icon { font-size: 14px; }
  .paste-curl-input {
    width: 100%;
    background: var(--bg);
    border: 1.5px dashed var(--accent-border);
    border-radius: 8px;
    padding: 10px 12px;
    color: var(--text-2);
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px;
    resize: none;
    line-height: 1.5;
    transition: border-color 0.15s;
    box-sizing: border-box;
  }
  .paste-curl-input:focus {
    outline: none;
    border-color: var(--accent);
    border-style: solid;
  }
  .paste-curl-input::placeholder { color: var(--text-7); }
  .parse-err { margin: 0; font-size: 11px; color: var(--danger-text); }

  .divider {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--text-7);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .divider::before, .divider::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }
  .divider span { flex-shrink: 0; }

  .hint { margin: 0; font-size: 12px; color: var(--text-4); line-height: 1.5; }
  .hint-inline { font-size: 10px; color: var(--text-5); font-weight: 400; }
  .req { color: var(--danger-text); }

  fieldset.form {
    border: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 11px;
  }
  fieldset.form:disabled { opacity: 0.45; pointer-events: none; }

  .field-row { display: flex; gap: 10px; align-items: flex-end; }
  .field {
    display: flex; flex-direction: column; gap: 5px;
    font-size: 12px; color: var(--text-4);
  }
  .field input, .field select, .field textarea {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 7px 10px;
    color: var(--text-2);
    font-size: 12px;
    font-family: inherit;
    resize: vertical;
  }
  .field textarea { font-family: 'SF Mono', monospace; font-size: 11px; }
  .field input:focus, .field select:focus, .field textarea:focus {
    outline: none; border-color: var(--accent);
  }
  .section-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.6px; color: var(--text-6); margin-top: 2px;
  }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

  .curl-block { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .curl-head {
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 12px; border-bottom: 1px solid var(--border);
    font-size: 11px; color: var(--text-5);
  }
  .copy-btn {
    background: var(--surface); border: 1px solid var(--border); color: var(--text-4);
    border-radius: 4px; padding: 3px 9px; font-size: 11px; cursor: pointer;
  }
  .copy-btn:hover { border-color: var(--accent); color: var(--accent); }
  .curl-pre { margin: 0; padding: 12px; overflow-x: auto; }
  .curl-pre code {
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px; color: #7ec87e; white-space: pre; line-height: 1.6;
  }

  .err { color: var(--danger-text); font-size: 12px; margin: 0; }

  .field-label-text { font-size: 12px; color: var(--text-4); margin-bottom: 4px; display: block; }
  .extra-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
  .field-input {
    flex: 1; background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 9px; color: var(--text-2); font-size: 12px; font-family: inherit;
  }
  .field-input:focus { outline: none; border-color: var(--accent); }
  .rm-btn {
    background: none; border: 1px solid var(--border-3); border-radius: 5px;
    color: var(--text-5); font-size: 11px; padding: 4px 7px; cursor: pointer; flex-shrink: 0;
  }
  .rm-btn:hover { border-color: var(--danger-border); color: var(--danger-text); }
  .add-mapping-btn {
    background: none; border: 1px dashed var(--border-3); border-radius: 5px;
    color: var(--text-5); font-size: 11px; padding: 5px 12px; cursor: pointer; margin-top: 2px;
  }
  .add-mapping-btn:hover { border-color: var(--accent); color: var(--accent); }

  .footer {
    border-top: 1px solid var(--border);
    padding-top: 14px;
    margin-top: 2px;
  }

  .actions { display: flex; justify-content: flex-end; gap: 10px; }

  .btn-copy-curl {
    background: transparent;
    color: var(--accent);
    border: 1px solid var(--accent-border);
    border-radius: 7px;
    padding: 9px 16px;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
    margin-right: auto;
  }
  .btn-copy-curl:disabled { opacity: 0.3; cursor: not-allowed; }
  .btn-copy-curl:not(:disabled):hover { background: var(--accent-dim); border-color: var(--accent); }

  .btn-rebuild {
    background: var(--accent); color: #fff; border: none;
    border-radius: 7px; padding: 9px 20px;
    font-size: 13px; font-weight: 600; cursor: pointer;
  }
  .btn-rebuild:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-rebuild:not(:disabled):hover { background: var(--accent-hover); }

  .btn-cancel {
    background: transparent; color: var(--text-4); border: 1px solid var(--border-3);
    border-radius: 7px; padding: 9px 18px; font-size: 13px; cursor: pointer;
  }
  .btn-cancel:hover { border-color: var(--text-6); color: var(--text-3); }

  .progress-section {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 2px 0 4px;
  }

  .progress-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .stage-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .pulse-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--accent);
    animation: pulse 1.4s ease-in-out infinite;
    flex-shrink: 0;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(0.75); }
  }

  .stage-text { font-size: 13px; color: var(--text-2); font-weight: 500; }

  .pct-label { font-size: 13px; font-weight: 600; color: var(--accent); font-variant-numeric: tabular-nums; }

  .progress-bar {
    width: 100%; height: 5px;
    background: var(--border);
    border-radius: 3px;
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent-hover), var(--accent));
    border-radius: 3px;
    transition: width 0.5s ease;
  }

  .doc-counter {
    display: flex;
    gap: 4px;
    align-items: baseline;
    font-size: 12px;
  }
  .doc-done { color: var(--accent); font-weight: 600; font-variant-numeric: tabular-nums; }
  .doc-sep { color: var(--text-7); }
  .doc-total { color: var(--text-5); }

  .done-section {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }

  .done-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .done-check {
    width: 22px; height: 22px;
    border-radius: 50%;
    background: #0f2a1a;
    border: 1.5px solid #2d6a44;
    color: #5cdb85;
    font-size: 12px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }

  .done-text { font-size: 13px; color: var(--text-3); line-height: 1.5; }
  .done-text strong { color: var(--text-2); }
  .warn-check {
    width: 22px; height: 22px;
    border-radius: 50%;
    background: #2a1a00;
    border: 1.5px solid #7a4a00;
    color: #f0a040;
    font-size: 13px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }
  .warn-text { color: var(--text-3); }
  .warn-text strong { color: #f0a040; }
  .arrow-sep { color: var(--text-5); }
  input[type="checkbox"] { accent-color: var(--accent); }
</style>
