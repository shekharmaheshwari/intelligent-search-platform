<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  /**
   * ApiPanel — API reference card for a specific index.
   *
   * Shown in the IndexDetail "API" tab. Renders copy-ready curl examples for
   * both the internal (full) endpoints and the public (no-vec) endpoints.
   * The base URL is derived at runtime from ``window.location`` so examples
   * point at the correct host in both local and deployed environments.
   */
  import { getApiBase, buildApiCurls } from './ApiPanel.helpers';

  export let indexId: string;
  /** Human-readable identifier, e.g. "accessories". Falls back to indexId when unset (legacy indexes). */
  export let slug: string = '';

  const base = getApiBase();
  // Prefer the slug everywhere an identifier is shown or sent — every endpoint below
  // resolves either form, but the slug reads far better in docs and curl examples.
  const id    = slug || indexId;
  const curls = buildApiCurls(base, id);

  const curlPublicSearch = curls.publicSearch;
  const curlPublicDocs   = curls.publicDocs;
  const curlSuggest      = curls.suggest;
  const curlHybrid       = curls.hybrid;
  const curlLexical      = curls.lexical;
  const curlDocs         = curls.docs;
  const curlChat         = curls.chat;
  const curlChatTurn     = curls.chatTurn;

  let copied = '';

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
          <span class="path">/v1/indexes/{id}/search/public</span>
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
          <span class="path">/v1/indexes/{id}/documents/public</span>
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

    <section>
      <div class="section-head">
        <div class="endpoint-line">
          <span class="method get">GET</span>
          <span class="path">/v1/suggest</span>
        </div>
        <button class="copy-btn" on:click={() => copy(curlSuggest, 'suggest')}>
          {copied === 'suggest' ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <p class="desc">
        Autocomplete / typeahead suggestions — already unauthenticated, no <code>/public</code>
        variant needed. Note the URL shape differs from the endpoints above: it's not nested
        under <code>/indexes/{'{'}id{'}'}</code> — pass the index via the <code>index_id</code> query param instead.
      </p>
      <pre class="code-block"><code>{curlSuggest}</code></pre>
      <p class="desc">
        Query params: <code>q</code> (required, min 2 chars), <code>index_id</code>,
        <code>limit</code> (default 8, max 20), <code>session_id</code> (optional).
      </p>
      <p class="desc">
        Scope to one category via <code>index_id</code> (resolved server-side to that
        index's <code>intent_label</code>) or <code>category</code> directly (e.g. isolated
        stores). When scoped, results are strictly limited to that category — no
        cross-category fallback.
      </p>
      <pre class="code-block"><code>{`{
  "suggestions": [
    { "text": "iphone 17 pro case", "type": "query", "score": 12.4 }
  ],
  "query": "iphone"
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
          <span class="path">/v1/indexes/{id}/search</span>
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
          <span class="path">/v1/indexes/{id}/search</span>
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
          <span class="path">/v1/indexes/{id}/search</span>
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
          <span class="path">/v1/indexes/{id}/documents</span>
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

  <div class="divider"></div>

  <!-- ── Chat API ───────────────────────────────────────────────────────── -->
  <div class="api-group">
    <div class="group-header">
      <span class="group-title">Chat API</span>
      <span class="group-badge chat-badge">Streaming · SSE · RAG-grounded</span>
    </div>
    <p class="group-desc">
      Conversational endpoint grounded in this index. Returns a
      <a href="https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events" target="_blank" rel="noopener">Server-Sent Events</a>
      stream — each <code>data:</code> line is a JSON chunk. Send the full
      <code>messages</code> history on every turn to maintain context.
    </p>

    <section>
      <div class="section-head">
        <div class="endpoint-line">
          <span class="method post">POST</span>
          <span class="path">/v1/indexes/{id}/chat</span>
        </div>
        <button class="copy-btn" on:click={() => copy(curlChat, 'chat')}>
          {copied === 'chat' ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <p class="desc">First turn — empty <code>messages</code> array.</p>
      <pre class="code-block"><code>{curlChat}</code></pre>
    </section>

    <section>
      <div class="section-head">
        <div class="endpoint-line">
          <span class="method post">POST</span>
          <span class="path">/v1/indexes/{id}/chat</span>
          <span class="mode-tag">multi-turn</span>
        </div>
        <button class="copy-btn" on:click={() => copy(curlChatTurn, 'chat-turn')}>
          {copied === 'chat-turn' ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <p class="desc">Follow-up turn — pass prior <code>messages</code> for context.</p>
      <pre class="code-block"><code>{curlChatTurn}</code></pre>
    </section>

    <section>
      <div class="section-head"><span class="section-label">Request body</span></div>
      <pre class="code-block"><code>{`{
  "query":    "string",   // user's question (required)
  "messages": [           // prior turns — omit or [] on first turn
    {
      "role":     "user" | "assistant",
      "content":  "string",
      "products": [...]   // assistant turns only — shown product cards
    }
  ]
}`}</code></pre>
    </section>

    <section>
      <div class="section-head"><span class="section-label">SSE response stream</span></div>
      <pre class="code-block"><code>{`data: {"type": "products", "items": [...]}   // product cards (first chunk)
data: {"type": "text", "content": "Here"}    // streamed answer tokens
data: {"type": "text", "content": " are"}
data: {"type": "text", "content": " some…"}
data: [DONE]                                 // stream closed`}</code></pre>
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
  .public-badge   { background: #0d2a1f; color: #4cde93; border: 1px solid #1a4a30; }
  .internal-badge { background: #1a1a2e; color: #7c7caa; border: 1px solid #2a2a44; }
  .chat-badge     { background: #1e1130; color: #c084fc; border: 1px solid #3b1f5e; }

  .group-desc { font-size: 12px; color: var(--text-5); margin: 0; line-height: 1.6; }
  .group-desc code { color: var(--accent); background: var(--accent-dim); padding: 1px 4px; border-radius: 3px; font-size: 11px; }
  .group-desc a { color: var(--accent); text-decoration: underline; }

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
