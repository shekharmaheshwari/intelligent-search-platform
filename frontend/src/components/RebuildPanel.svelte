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
  import { api } from '../lib/api';
  import type { IndexMeta, IngestStatus, RestConnectorConfig } from '../lib/types';

  export let index: IndexMeta;

  const dispatch = createEventDispatcher<{ done: void; close: void }>();

  /** Pre-populate form fields from the first saved REST connector on this index, if any. */
  function getExistingRest(): Partial<RestConnectorConfig> {
    // Only pre-populate from a saved REST connector — never from external connectors
    // (external connectors have type:'external' and url is a product page prefix, not an API)
    const c = index.connectors.find(c => c.type === 'rest');
    if (!c) return {};
    return {
      url:     String(c.url ?? ''),
      method:  String(c.method ?? 'GET'),
      headers: (c.headers as Record<string,string>) ?? {},
      body:    String(c.body ?? ''),
    };
  }

  const pre = getExistingRest();

  let url          = pre.url ?? '';
  let method       = pre.method ?? 'GET';
  let headersRaw   = pre.headers ? JSON.stringify(pre.headers, null, 2) : '{}';
  let body         = pre.body ?? '';

  // ── Curl paste parser ─────────────────────────────────────────────────────
  const curlPlaceholder = `curl -X POST 'https://api.example.com/items' -H 'Authorization: Bearer TOKEN' -d '{"key": "value"}'`;
  let curlInput = '';
  let parseError = '';

  /**
   * Parse a raw curl command string and populate the form fields.
   * Extracts the HTTP method, URL, headers (-H flags), and request body (-d/--data).
   * Sets ``parseError`` if the string does not start with "curl" or has no URL.
   */
  function parseCurl(raw: string) {
    parseError = '';
    const s = raw.replace(/\\\n/g, ' ').replace(/\s+/g, ' ').trim();
    if (!s.startsWith('curl')) { parseError = 'Must start with "curl"'; return; }

    // Method
    const mMatch = s.match(/-X\s+([A-Z]+)/);
    const parsedMethod = mMatch ? mMatch[1] : 'GET';

    // URL — first bare https?:// token or quoted URL
    const urlMatch = s.match(/curl(?:\s+-[^\s]+)*\s+['"]?(https?:\/\/[^\s'"\\]+)['"]?/);
    if (!urlMatch) { parseError = 'Could not find URL'; return; }
    const parsedUrl = urlMatch[1];

    // Headers
    const hdrs: Record<string, string> = {};
    const hRegex = /-H\s+['"]([^'"]+)['"]/g;
    let hm: RegExpExecArray | null;
    while ((hm = hRegex.exec(s)) !== null) {
      const colon = hm[1].indexOf(':');
      if (colon > 0) {
        const k = hm[1].slice(0, colon).trim();
        const v = hm[1].slice(colon + 1).trim();
        hdrs[k] = v;
      }
    }

    // Body — -d or --data
    const dMatch = s.match(/(?:--data|-d)\s+'([^']*)'/) ||
                   s.match(/(?:--data|-d)\s+"((?:[^"\\]|\\.)*)"/) ||
                   s.match(/(?:--data|-d)\s+(\{[^}]+\})/);
    const parsedBody = dMatch ? dMatch[1].replace(/\\"/g, '"') : '';

    // Apply
    method     = parsedMethod;
    url        = parsedUrl;
    headersRaw = JSON.stringify(hdrs, null, 2);
    body       = parsedBody;
    curlInput  = '';   // clear the textarea after parse
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

  /** Build a multi-line curl command preview from the current form values. Returns a placeholder comment when URL is empty. */
  function buildCurl(u: string, m: string, h: string, b: string): string {
    if (!u) return '# Enter a URL above to see the curl command';
    const lines: string[] = [`curl -X ${m} "${u}"`];
    try {
      const hdrs = JSON.parse(h || '{}');
      for (const [k, v] of Object.entries(hdrs)) {
        lines.push(`  -H "${k}: ${v}"`);
      }
    } catch { /* ignore */ }
    if (b.trim()) {
      if (!h.includes('Content-Type')) {
        lines.push(`  -H "Content-Type: application/json"`);
      }
      lines.push(`  -d '${b.trim()}'`);
    }
    return lines.join(' \\\n');
  }

  /** Copy the current curl preview to the clipboard and show a brief "Copied" confirmation. */
  function copyCurl() {
    navigator.clipboard.writeText(curlCmd);
    copied = true;
    setTimeout(() => { copied = false; }, 1500);
  }

  /** Validate the form, call the rebuild API, then start the status-poll loop. */
  async function runRebuild() {
    if (!url.trim()) { errMsg = 'URL is required'; return; }
    let headers: Record<string, string> = {};
    try { headers = JSON.parse(headersRaw || '{}'); } catch { errMsg = 'Invalid JSON in headers'; return; }

    errMsg = '';
    runState = 'running';
    stage = 'Fetching content…';
    statusIndexed = 0;
    statusTotal = 0;

    const cfg: RestConnectorConfig = {
      url: url.trim(), method, headers,
      body: body.trim(),
    };

    try {
      await api.rebuild(index.id, cfg);
      poll();
    } catch (e) {
      runState = 'error';
      errMsg = String(e);
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
        const s: IngestStatus = await api.getStatus(index.id);
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
    <!-- Paste curl shortcut —— pastes the whole curl and fills all fields -->
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

    <!-- Form — disabled while running -->
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

    {#if errMsg && runState !== 'running'}
      <p class="err">{errMsg}</p>
    {/if}

    <!-- Actions + inline progress -->
    <div class="footer">
      {#if runState === 'idle' || runState === 'error'}
        <div class="actions">
          <button class="btn-cancel" on:click={() => dispatch('close')}>Cancel</button>
          <button class="btn-copy-curl" on:click={copyCurl} disabled={!url.trim()} title="Copy full curl command">
            {copied ? '✓ Copied' : '⎘ Copy curl'}
          </button>
          <button class="btn-rebuild" on:click={runRebuild} disabled={!url.trim()}>
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
    background: #111;
    border: 1px solid #222;
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
    border-bottom: 1px solid #1e1e1e;
    flex-shrink: 0;
  }
  .panel-title {
    display: flex; align-items: center; gap: 8px;
    font-size: 14px; font-weight: 600; color: #eee;
  }
  .icon { color: #7c6cf4; font-size: 16px; }
  .close { background: none; border: none; color: #555; cursor: pointer; font-size: 14px; }
  .close:hover { color: #aaa; }

  .body {
    padding: 20px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  /* Paste curl */
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
    color: #9d91f5;
  }
  .paste-icon { font-size: 14px; }
  .paste-curl-input {
    width: 100%;
    background: #0d0d0d;
    border: 1.5px dashed #3a3070;
    border-radius: 8px;
    padding: 10px 12px;
    color: #bbb;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px;
    resize: none;
    line-height: 1.5;
    transition: border-color 0.15s;
    box-sizing: border-box;
  }
  .paste-curl-input:focus {
    outline: none;
    border-color: #7c6cf4;
    border-style: solid;
  }
  .paste-curl-input::placeholder { color: #333; }
  .parse-err { margin: 0; font-size: 11px; color: #e55; }

  .divider {
    display: flex;
    align-items: center;
    gap: 10px;
    color: #333;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .divider::before, .divider::after {
    content: '';
    flex: 1;
    height: 1px;
    background: #1e1e1e;
  }
  .divider span { flex-shrink: 0; }

  .hint { margin: 0; font-size: 12px; color: #666; line-height: 1.5; }
  .hint-inline { font-size: 10px; color: #555; font-weight: 400; }
  .req { color: #e55; }

  /* Form */
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
    font-size: 12px; color: #777;
  }
  .field input, .field select, .field textarea {
    background: #0d0d0d;
    border: 1px solid #252525;
    border-radius: 6px;
    padding: 7px 10px;
    color: #ddd;
    font-size: 12px;
    font-family: inherit;
    resize: vertical;
  }
  .field textarea { font-family: 'SF Mono', monospace; font-size: 11px; }
  .field input:focus, .field select:focus, .field textarea:focus {
    outline: none; border-color: #7c6cf4;
  }
  .section-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.6px; color: #444; margin-top: 2px;
  }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

  /* Curl */
  .curl-block { background: #0a0a0a; border: 1px solid #1e1e1e; border-radius: 8px; overflow: hidden; }
  .curl-head {
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 12px; border-bottom: 1px solid #1a1a1a;
    font-size: 11px; color: #555;
  }
  .copy-btn {
    background: #161616; border: 1px solid #252525; color: #777;
    border-radius: 4px; padding: 3px 9px; font-size: 11px; cursor: pointer;
  }
  .copy-btn:hover { border-color: #7c6cf4; color: #9d91f5; }
  .curl-pre { margin: 0; padding: 12px; overflow-x: auto; }
  .curl-pre code {
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px; color: #7ec87e; white-space: pre; line-height: 1.6;
  }

  .err { color: #e55; font-size: 12px; margin: 0; }

  /* Footer */
  .footer {
    border-top: 1px solid #1a1a1a;
    padding-top: 14px;
    margin-top: 2px;
  }

  .actions { display: flex; justify-content: flex-end; gap: 10px; }

  .btn-copy-curl {
    background: transparent;
    color: #9d91f5;
    border: 1px solid #3a3070;
    border-radius: 7px;
    padding: 9px 16px;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
    margin-right: auto;
  }
  .btn-copy-curl:disabled { opacity: 0.3; cursor: not-allowed; }
  .btn-copy-curl:not(:disabled):hover { background: #1e1b3a; border-color: #7c6cf4; }

  .btn-rebuild {
    background: #7c6cf4; color: #fff; border: none;
    border-radius: 7px; padding: 9px 20px;
    font-size: 13px; font-weight: 600; cursor: pointer;
  }
  .btn-rebuild:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-rebuild:not(:disabled):hover { background: #6b5ce7; }

  .btn-cancel {
    background: transparent; color: #777; border: 1px solid #2a2a2a;
    border-radius: 7px; padding: 9px 18px; font-size: 13px; cursor: pointer;
  }
  .btn-cancel:hover { border-color: #444; color: #aaa; }

  /* ── Progress section ── */
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
    background: #7c6cf4;
    animation: pulse 1.4s ease-in-out infinite;
    flex-shrink: 0;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(0.75); }
  }

  .stage-text { font-size: 13px; color: #bbb; font-weight: 500; }

  .pct-label { font-size: 13px; font-weight: 600; color: #7c6cf4; font-variant-numeric: tabular-nums; }

  .progress-bar {
    width: 100%; height: 5px;
    background: #1e1e1e;
    border-radius: 3px;
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: linear-gradient(90deg, #5a4fd4, #9d91f5);
    border-radius: 3px;
    transition: width 0.5s ease;
  }

  .doc-counter {
    display: flex;
    gap: 4px;
    align-items: baseline;
    font-size: 12px;
  }
  .doc-done { color: #9d91f5; font-weight: 600; font-variant-numeric: tabular-nums; }
  .doc-sep { color: #333; }
  .doc-total { color: #555; }

  /* ── Done section ── */
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

  .done-text { font-size: 13px; color: #888; line-height: 1.5; }
  .done-text strong { color: #ccc; }
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
  .warn-text { color: #999; }
  .warn-text strong { color: #f0a040; }
</style>
