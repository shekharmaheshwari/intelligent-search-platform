<script lang="ts">
  /**
   * ApiPanel — API reference card for a specific index.
   *
   * Shown in the IndexDetail "API" tab. Renders copy-ready curl examples for
   * both the internal (full) endpoints and the public (no-vec) endpoints.
   * The base URL is derived at runtime from ``window.location`` so examples
   * point at the correct host in both local and deployed environments.
   */
  export let indexId: string;

  const base = typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:8001/v1`
    : 'http://localhost:8001/v1';

  // ── Public endpoints ──────────────────────────────────────────────────────
  const curlPublicSearch = `curl -X POST "${base}/indexes/${indexId}/search/public" \\
  -H "Content-Type: application/json" \\
  -d '{"q": "your query", "mode": "hybrid", "page": 1, "per_page": 10}'`;

  const curlPublicDocs = `curl "${base}/indexes/${indexId}/documents/public?page=1&per_page=20"`;

  // ── Internal endpoints ────────────────────────────────────────────────────
  const curlHybrid = `curl -X POST "${base}/indexes/${indexId}/search" \\
  -H "Content-Type: application/json" \\
  -d '{"q": "your query", "mode": "hybrid", "page": 1, "per_page": 10}'`;

  const curlLexical = `curl -X POST "${base}/indexes/${indexId}/search" \\
  -H "Content-Type: application/json" \\
  -d '{"q": "your query", "mode": "lexical", "page": 1, "per_page": 10}'`;

  const curlDocs = `curl "${base}/indexes/${indexId}/documents?page=1&per_page=20"`;

  let copied = '';

  /** Copy ``text`` to the clipboard and show a brief confirmation badge identified by ``key``. */
  function copy(text: string, key: string) {
    navigator.clipboard.writeText(text);
    copied = key;
    setTimeout(() => { copied = ''; }, 1500);
  }
</script>

<div class="api-panel">

  <!-- ── Public API ──────────────────────────────────────────────────────── -->
  <div class="api-group">
    <div class="group-header">
      <span class="group-title">Public API</span>
      <span class="group-badge public-badge">No embeddings · For external consumers</span>
    </div>
    <p class="group-desc">
      Use these endpoints from external applications. The <code>vec</code> embedding
      field is stripped before the response is sent, saving ~4 KB per document
      (1 024 floats). Ranking is identical to the internal endpoints.
    </p>

    <section>
      <div class="section-head">
        <div class="endpoint-line">
          <span class="method post">POST</span>
          <span class="path">/v1/indexes/{indexId}/search/public</span>
        </div>
        <button class="copy-btn" on:click={() => copy(curlPublicSearch, 'pub-search')}>
          {copied === 'pub-search' ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <p class="desc">Hybrid / lexical / semantic search — response excludes <code>vec</code>.</p>
      <pre class="code-block"><code>{curlPublicSearch}</code></pre>
    </section>

    <section>
      <div class="section-head">
        <div class="endpoint-line">
          <span class="method get">GET</span>
          <span class="path">/v1/indexes/{indexId}/documents/public</span>
        </div>
        <button class="copy-btn" on:click={() => copy(curlPublicDocs, 'pub-docs')}>
          {copied === 'pub-docs' ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <p class="desc">Paginated document listing — response excludes <code>vec</code>.</p>
      <pre class="code-block"><code>{curlPublicDocs}</code></pre>
    </section>

    <section>
      <div class="section-head"><span class="section-label">Public response format</span></div>
      <pre class="code-block"><code>{`{
  "hits": [
    {
      "document": {
        "id":          "abc123",
        "title":       "iPhone 17e",
        "content":     "iPhone 17e by Apple. iOS smartphone.",
        "url":         "https://www.example.com/cell-phone/apple-iphone-17e",
        "source_type": "device",
        "indexed_at":  1749859200,
        "popularity":  660,
        "is_featured": false,
        "metadata":    "{\"manufacturer\":\"Apple\",\"price\":499.99,...}"
        // vec is omitted
      }
    }
  ],
  "found": 42,
  "page": 1
}`}</code></pre>
    </section>
  </div>

  <div class="divider"></div>

  <!-- ── Internal API ────────────────────────────────────────────────────── -->
  <div class="api-group">
    <div class="group-header">
      <span class="group-title">Internal API</span>
      <span class="group-badge internal-badge">Includes embeddings · ISP use only</span>
    </div>
    <p class="group-desc">
      Full documents including the <code>vec</code> field. Use these endpoints within
      ISP or when the raw embedding vector is needed.
    </p>

    <section>
      <div class="section-head">
        <div class="endpoint-line">
          <span class="method post">POST</span>
          <span class="path">/v1/indexes/{indexId}/search</span>
        </div>
      </div>
      <p class="desc">Request body — all fields optional except <code>q</code>.</p>
      <pre class="code-block"><code>{`{
  "q":        "string",   // search query
  "mode":     "hybrid",   // "hybrid" | "lexical" | "semantic"
  "page":     1,
  "per_page": 10
}`}</code></pre>
    </section>

    <section>
      <div class="section-head">
        <div class="endpoint-line">
          <span class="method post">POST</span>
          <span class="path">/v1/indexes/{indexId}/search</span>
          <span class="mode-tag">hybrid</span>
        </div>
        <button class="copy-btn" on:click={() => copy(curlHybrid, 'hybrid')}>
          {copied === 'hybrid' ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <pre class="code-block"><code>{curlHybrid}</code></pre>
    </section>

    <section>
      <div class="section-head">
        <div class="endpoint-line">
          <span class="method post">POST</span>
          <span class="path">/v1/indexes/{indexId}/search</span>
          <span class="mode-tag">lexical</span>
        </div>
        <button class="copy-btn" on:click={() => copy(curlLexical, 'lexical')}>
          {copied === 'lexical' ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <pre class="code-block"><code>{curlLexical}</code></pre>
    </section>

    <section>
      <div class="section-head">
        <div class="endpoint-line">
          <span class="method get">GET</span>
          <span class="path">/v1/indexes/{indexId}/documents</span>
        </div>
        <button class="copy-btn" on:click={() => copy(curlDocs, 'docs')}>
          {copied === 'docs' ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <p class="desc">Paginated listing sorted by recency. Query params: <code>page</code>, <code>per_page</code>.</p>
      <pre class="code-block"><code>{curlDocs}</code></pre>
    </section>

    <section>
      <div class="section-head"><span class="section-label">Internal response format</span></div>
      <pre class="code-block"><code>{`{
  "hits": [
    {
      "document": {
        "id":          "abc123",
        "title":       "iPhone 17e",
        "content":     "...",
        "url":         "https://...",
        "source_type": "device",
        "indexed_at":  1749859200,
        "vec":         [0.023, -0.041, ...] // 1 024 floats
      }
    }
  ],
  "found": 42,
  "page": 1
}`}</code></pre>
    </section>
  </div>

</div>

<style>
  .api-panel { display: flex; flex-direction: column; gap: 0; }

  .api-group { display: flex; flex-direction: column; gap: 18px; padding: 4px 0 24px; }

  .group-header {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  }
  .group-title { font-size: 13px; font-weight: 700; color: var(--text-2); }
  .group-badge {
    font-size: 10px; font-weight: 600; padding: 2px 8px;
    border-radius: 10px; letter-spacing: 0.03em;
  }
  .public-badge  { background: #0d2a1f; color: #4cde93; border: 1px solid #1a4a30; }
  .internal-badge { background: #1a1a2e; color: #7c7caa; border: 1px solid #2a2a44; }

  .group-desc { font-size: 12px; color: var(--text-5); margin: 0; line-height: 1.6; }
  .group-desc code { color: var(--accent); background: var(--accent-dim); padding: 1px 4px; border-radius: 3px; font-size: 11px; }

  .divider { border: none; border-top: 1px solid var(--border); margin: 8px 0 24px; }

  section { display: flex; flex-direction: column; gap: 8px; }

  .section-head {
    display: flex; justify-content: space-between; align-items: center; gap: 12px;
  }
  .section-label { font-size: 12px; color: var(--text-5); }

  .endpoint-line {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0;
  }

  .method {
    font-size: 10px; font-weight: 700; padding: 2px 6px;
    border-radius: 4px; flex-shrink: 0; letter-spacing: 0.04em;
  }
  .post { background: #1e2a40; color: #70a8ff; }
  .get  { background: #0d2a1f; color: #4cde93; }

  .path {
    font-size: 12px; color: var(--text-3); font-family: 'SF Mono', 'Fira Code', monospace;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }

  .mode-tag {
    font-size: 10px; color: var(--text-4); background: var(--surface);
    border: 1px solid var(--border-2); border-radius: 4px; padding: 1px 6px; flex-shrink: 0;
  }

  .desc { font-size: 12px; color: var(--text-5); margin: 0; }
  .desc code { color: var(--accent); background: var(--accent-dim); padding: 1px 4px; border-radius: 3px; font-size: 11px; }

  .code-block {
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 7px; padding: 14px 16px;
    overflow-x: auto; margin: 0;
  }
  code {
    font-size: 12px; color: #a8d8a8;
    font-family: 'SF Mono', 'Fira Code', monospace; white-space: pre;
  }

  .copy-btn {
    background: var(--surface); border: 1px solid var(--border-2);
    color: var(--text-4); border-radius: 5px;
    padding: 4px 10px; font-size: 11px; cursor: pointer; flex-shrink: 0;
  }
  .copy-btn:hover { border-color: var(--accent); color: var(--accent); }
</style>
