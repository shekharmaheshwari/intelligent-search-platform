<script lang="ts">
  /**
   * ApiPanel — API reference card for a specific index.
   *
   * Shown in the IndexDetail "API" tab.  Renders copy-ready curl examples for
   * hybrid and lexical search, plus the expected response JSON shape.
   * The base URL is derived at runtime from ``window.location`` so examples
   * point at the correct host in both local and deployed environments.
   */
  export let indexId: string;

  const base = typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:8001/v1`
    : 'http://localhost:8001/v1';

  const curlHybrid = `curl -X POST "${base}/indexes/${indexId}/search" \\
  -H "Content-Type: application/json" \\
  -d '{"q": "your query", "mode": "hybrid", "page": 1, "per_page": 10}'`;

  const curlLexical = `curl -X POST "${base}/indexes/${indexId}/search" \\
  -H "Content-Type: application/json" \\
  -d '{"q": "your query", "mode": "lexical", "page": 1, "per_page": 10}'`;

  let copied = '';

  /** Copy ``text`` to the clipboard and show a brief confirmation badge identified by ``key``. */
  function copy(text: string, key: string) {
    navigator.clipboard.writeText(text);
    copied = key;
    setTimeout(() => { copied = ''; }, 1500);
  }
</script>

<div class="api-panel">
  <p class="intro">Use these endpoints to integrate search into your application.</p>

  <section>
    <div class="section-head">
      <h3>POST /v1/indexes/{indexId}/search</h3>
    </div>
    <p class="desc">Search documents in this index. Supports hybrid, lexical, and semantic modes.</p>

    <div class="request-body">
      <pre><code>{`{
  "q": "string",         // search query
  "mode": "hybrid",      // "hybrid" | "lexical" | "semantic"
  "page": 1,
  "per_page": 10
}`}</code></pre>
    </div>
  </section>

  <section>
    <div class="section-head">
      <span>Hybrid search</span>
      <button class="copy-btn" on:click={() => copy(curlHybrid, 'hybrid')}>
        {copied === 'hybrid' ? '✓ Copied' : 'Copy'}
      </button>
    </div>
    <pre class="code-block"><code>{curlHybrid}</code></pre>
  </section>

  <section>
    <div class="section-head">
      <span>Lexical search</span>
      <button class="copy-btn" on:click={() => copy(curlLexical, 'lexical')}>
        {copied === 'lexical' ? '✓ Copied' : 'Copy'}
      </button>
    </div>
    <pre class="code-block"><code>{curlLexical}</code></pre>
  </section>

  <section>
    <h3>Response format</h3>
    <pre class="code-block"><code>{`{
  "hits": [
    {
      "document": {
        "id": "abc123",
        "title": "Page Title",
        "content": "Page content text…",
        "url": "https://example.com/page",
        "source_type": "sitemap",
        "indexed_at": 1717000000
      }
    }
  ],
  "found": 42,
  "page": 1
}`}</code></pre>
  </section>
</div>

<style>
  .api-panel { display: flex; flex-direction: column; gap: 24px; }
  .intro { font-size: 13px; color: #777; margin: 0; }
  section { display: flex; flex-direction: column; gap: 10px; }
  h3 { font-size: 13px; color: #aaa; font-weight: 500; margin: 0; font-family: monospace; }
  .section-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .section-head span { font-size: 13px; color: #aaa; }
  .desc { font-size: 12px; color: #666; margin: 0; }
  .code-block, .request-body pre {
    background: #0d0d0d;
    border: 1px solid #1e1e1e;
    border-radius: 7px;
    padding: 14px 16px;
    overflow-x: auto;
    margin: 0;
  }
  code {
    font-size: 12px;
    color: #a8d8a8;
    font-family: 'SF Mono', 'Fira Code', monospace;
    white-space: pre;
  }
  .copy-btn {
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    color: #888;
    border-radius: 5px;
    padding: 4px 10px;
    font-size: 11px;
    cursor: pointer;
  }
  .copy-btn:hover { border-color: #7c6cf4; color: #9d91f5; }
</style>
