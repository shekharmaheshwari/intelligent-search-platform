<script lang="ts">
  import { onMount } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { api } from '../lib/api';
  import type { IndexMeta, DocumentItem } from '../lib/types';
  import RebuildPanel from './RebuildPanel.svelte';
  import IndexExtensionsPanel from './IndexExtensionsPanel.svelte';

  export let index: IndexMeta;

  const dispatch = createEventDispatcher<{ configure: string }>();

  let showRebuild = false;
  let showExtensions = false;

  let docs: DocumentItem[] = [];
  let found = 0;
  let page = 1;
  const perPage = 20;
  let loadingDocs = false;

  let q = '';
  let searchMode: 'hybrid' | 'lexical' | 'semantic' = 'hybrid';
  const searchModes: Array<'hybrid' | 'lexical' | 'semantic'> = ['hybrid', 'lexical', 'semantic'];
  let searching = false;
  let searched = false;

  async function loadDocs() {
    loadingDocs = true;
    try {
      const res = await api.listDocuments(index.id, page, perPage);
      docs = res.hits.map(h => h.document);
      found = res.found;
    } finally {
      loadingDocs = false;
    }
  }

  async function search() {
    if (!q.trim()) { searched = false; loadDocs(); return; }
    searching = true; searched = true;
    try {
      const res = await api.search(index.id, q.trim(), searchMode);
      docs = res.hits.map(h => h.document);
      found = res.found;
    } finally {
      searching = false;
    }
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') search();
  }

  function clearSearch() {
    q = '';
    searched = false;
    loadDocs(); // reload default list when user clears via ✕
  }

  function onSearchClick() {
    if (!q.trim()) { clearSearch(); return; }
    search();
  }

  let _loadedIndexId = '';
  $: if (index && index.id !== _loadedIndexId) {
    _loadedIndexId = index.id;
    page = 1; q = ''; searched = false; loadDocs();
  }

  function excerpt(text: string, max = 180) {
    return text.length > max ? text.slice(0, max) + '…' : text;
  }

  function fmtDate(ts: number) {
    return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function connectorUrl(idx: IndexMeta): string {
    if (!idx.connectors.length) return '';
    return String(idx.connectors[0].url ?? '');
  }

  let expandedIds = new Set<string>();
  function toggleExpand(id: string) {
    if (expandedIds.has(id)) expandedIds.delete(id);
    else expandedIds.add(id);
    expandedIds = expandedIds; // trigger reactivity
  }

  function docFields(doc: DocumentItem): [string, unknown][] {
    return Object.entries(doc);
  }

  function tryParseJson(v: unknown): Record<string, unknown> | null {
    if (!v || typeof v !== 'string') return null;
    const s = v.trim();
    if (!s.startsWith('{')) return null;
    try { return JSON.parse(s); } catch { return null; }
  }

  function isMetadataKey(key: string): boolean {
    return key === 'metadata';
  }

  function formatValue(v: unknown): string {
    if (v === null || v === undefined) return '';
    if (typeof v === 'string') return v;
    return JSON.stringify(v, null, 2);
  }

  function formatMetaValue(v: unknown): string {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'boolean') return v ? 'Yes' : 'No';
    if (Array.isArray(v)) return v.join(', ') || '—';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }

  function prev() { if (page > 1) { page--; searched ? search() : loadDocs(); } }
  function next() { if (page * perPage < found) { page++; searched ? search() : loadDocs(); } }
</script>

<div class="content">
  <!-- Source header -->
  <div class="source-header">
    <div class="source-title-row">
      <div class="source-title-info">
        <h1>{index.name}</h1>
        {#if index.description}
          <p class="source-desc">{index.description}</p>
        {/if}
        {#if connectorUrl(index)}
          <a class="source-url" href={connectorUrl(index)} target="_blank" rel="noopener">
            {connectorUrl(index)}
          </a>
        {/if}
      </div>
      <div class="source-actions">
        <div class="stat-pill">{index.doc_count.toLocaleString()} docs</div>
        <button class="ext-btn" on:click={() => showExtensions = true}>⚙ Extensions</button>
        <button class="rebuild-btn" on:click={() => showRebuild = true}>↺ Rebuild</button>
        <button class="configure-btn" on:click={() => dispatch('configure', index.id)}>Configure →</button>
      </div>
    </div>

  {#if showExtensions}
    <IndexExtensionsPanel indexId={index.id} on:close={() => showExtensions = false} />
  {/if}

  {#if showRebuild}
    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
    <div class="overlay" on:click|self={() => showRebuild = false} role="dialog" aria-modal="true">
      <RebuildPanel
        {index}
        on:close={() => showRebuild = false}
        on:done={() => { loadDocs(); }}
      />
    </div>
  {/if}

    <!-- Search bar -->
    <div class="search-bar">
      <div class="search-input-wrap">
        <span class="search-icon">⌕</span>
        <input
          bind:value={q}
          on:keydown={onKeydown}
          placeholder="Search content…"
          class="search-input"
        />
        {#if q}
          <button class="clear-btn" on:click={clearSearch}>✕</button>
        {/if}
      </div>
      <button class="search-btn" on:click={search}>Search</button>
      <div class="mode-toggle">
        {#each searchModes as m}
          <button class:sel={searchMode === m} on:click={() => { searchMode = m; }}>{m}</button>
        {/each}
      </div>
    </div>
  </div>

  <!-- Content list -->
  <div class="doc-list">
    {#if loadingDocs || searching}
      <div class="loading-row">
        <div class="spinner"></div>
        <span>Loading…</span>
      </div>
    {:else if docs.length === 0 && index.doc_count === 0}
      <div class="empty-state">
        <div class="empty-icon">⬡</div>
        <p>No content indexed yet.</p>
        <p class="empty-sub">Configure a connector and run an ingestion to populate this source.</p>
        <button class="configure-btn" on:click={() => dispatch('configure', index.id)}>Configure source →</button>
      </div>
    {:else if docs.length === 0}
      <div class="empty-state">
        <p>No results for "<strong>{q}</strong>"</p>
      </div>
    {:else}
      <div class="list-meta">
        {#if searched}
          <span>{found.toLocaleString()} results for "<strong>{q}</strong>"</span>
          <button class="clear-link" on:click={clearSearch}>Clear search</button>
        {:else}
          <span>Showing {(page - 1) * perPage + 1}–{Math.min(page * perPage, found)} of {found.toLocaleString()} documents</span>
        {/if}
      </div>

      {#each docs as doc}
        <div class="doc-item" class:expanded={expandedIds.has(doc.id)}>
          <div class="doc-header">
            <span class="doc-title">
              {#if doc.url}
                <a href={doc.url} target="_blank" rel="noopener">{doc.title || doc.url}</a>
              {:else}
                {doc.title || 'Untitled'}
              {/if}
            </span>
            <span class="doc-meta">
              <span class="source-type">{doc.source_type}</span>
              <span class="doc-date">{fmtDate(doc.indexed_at)}</span>
              <button
                class="expand-btn"
                on:click={() => toggleExpand(doc.id)}
                aria-label={expandedIds.has(doc.id) ? 'Collapse' : 'Expand'}
              >{expandedIds.has(doc.id) ? '−' : '+'}</button>
            </span>
          </div>
          {#if doc.url}
            <div class="doc-url">{doc.url}</div>
          {/if}
          {#if !expandedIds.has(doc.id)}
            <div class="doc-snippet">{excerpt(doc.content)}</div>
          {:else}
            <div class="doc-fields">
              {#each docFields(doc) as [key, val]}
                <div class="field-row">
                  <span class="field-key">{key}</span>
                  {#if key === 'vec' && Array.isArray(val)}
                    <textarea class="field-vec" readonly rows="4">{formatValue(val)}</textarea>
                  {:else if isMetadataKey(key)}
                    {@const parsed = tryParseJson(val)}
                    {#if parsed}
                      <div class="meta-block">
                        {#each Object.entries(parsed) as [mk, mv]}
                          <div class="meta-row">
                            <span class="meta-key">{mk}</span>
                            {#if mk === 'images' && Array.isArray(mv) && mv.length}
                              <div class="meta-images">
                                {#each mv.slice(0,3) as imgUrl}
                                  <img class="meta-thumb" src={String(imgUrl)} alt="" loading="lazy" />
                                {/each}
                              </div>
                            {:else}
                              <span class="meta-val" class:meta-price={mk === 'price'} class:meta-stock={mk === 'in_stock'}>
                                {formatMetaValue(mv)}
                              </span>
                            {/if}
                          </div>
                        {/each}
                      </div>
                    {:else}
                      <span class="field-val">{formatValue(val)}</span>
                    {/if}
                  {:else}
                    <span class="field-val">{formatValue(val)}</span>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/each}

      {#if found > perPage}
        <div class="pagination">
          <button on:click={prev} disabled={page === 1}>← Prev</button>
          <span>Page {page} of {Math.ceil(found / perPage)}</span>
          <button on:click={next} disabled={page * perPage >= found}>Next →</button>
        </div>
      {/if}
    {/if}
  </div>
</div>

<style>
  .content {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    height: calc(100vh - 56px);
    overflow-y: auto;
  }

  .source-header {
    padding: 24px 28px 0;
    border-bottom: 1px solid #1a1a1a;
    background: #0a0a0a;
    position: sticky;
    top: 0;
    z-index: 10;
  }

  .source-title-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    margin-bottom: 16px;
  }

  .source-title-info { display: flex; flex-direction: column; gap: 3px; min-width: 0; }

  h1 { margin: 0; font-size: 20px; font-weight: 600; color: #eee; }

  .source-desc { margin: 0; font-size: 13px; color: #666; }

  .source-url {
    font-size: 11px;
    color: #4a4880;
    font-family: 'SF Mono', monospace;
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    display: block;
    max-width: 100%;
  }
  .source-url:hover { color: #7c6cf4; text-decoration: underline; }

  .source-actions { display: flex; gap: 8px; align-items: center; flex-shrink: 0; flex-wrap: wrap; }

  .stat-pill {
    background: #141414;
    border: 1px solid #222;
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 12px;
    color: #666;
  }

  .ext-btn {
    background: #111;
    border: 1px solid #222;
    color: #666;
    border-radius: 7px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
  }
  .ext-btn:hover { border-color: #7c6cf4; color: #9d91f5; background: #141420; }

  .rebuild-btn {
    background: #1e1b3a;
    border: 1px solid #3a3070;
    color: #9d91f5;
    border-radius: 7px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
  }
  .rebuild-btn:hover { background: #2a2552; border-color: #7c6cf4; color: #bfb8ff; }

  .configure-btn {
    background: transparent;
    border: 1px solid #2a2a2a;
    color: #888;
    border-radius: 7px;
    padding: 6px 14px;
    font-size: 12px;
    cursor: pointer;
    white-space: nowrap;
  }
  .configure-btn:hover { border-color: #7c6cf4; color: #9d91f5; }

  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.75);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 300;
  }

  .search-bar {
    display: flex;
    gap: 10px;
    align-items: center;
    padding-bottom: 16px;
    flex-wrap: wrap;
  }

  .search-input-wrap {
    flex: 1;
    min-width: 200px;
    display: flex;
    align-items: center;
    background: #111;
    border: 1px solid #222;
    border-radius: 8px;
    padding: 0 10px;
    gap: 8px;
  }
  .search-input-wrap:focus-within { border-color: #7c6cf4; }

  .search-icon { font-size: 16px; color: #444; }

  .search-input {
    flex: 1;
    background: none;
    border: none;
    color: #eee;
    font-size: 13px;
    padding: 8px 0;
    outline: none;
  }
  .search-input::placeholder { color: #444; }

  .clear-btn {
    background: none;
    border: none;
    color: #444;
    cursor: pointer;
    font-size: 12px;
    padding: 0;
  }
  .clear-btn:hover { color: #888; }

  .search-btn {
    background: #7c6cf4;
    border: none;
    color: #fff;
    border-radius: 7px;
    padding: 7px 16px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    flex-shrink: 0;
    white-space: nowrap;
  }
  .search-btn:hover { background: #6b5ce7; }

  .mode-toggle {
    display: flex;
    border: 1px solid #222;
    border-radius: 7px;
    overflow: hidden;
    flex-shrink: 0;
  }
  .mode-toggle button {
    background: transparent;
    border: none;
    color: #555;
    padding: 7px 12px;
    font-size: 11px;
    cursor: pointer;
    text-transform: capitalize;
    transition: background 0.1s;
  }
  .mode-toggle button.sel { background: #7c6cf4; color: #fff; }
  .mode-toggle button:hover:not(.sel) { color: #999; }

  /* Doc list */
  .doc-list {
    padding: 20px 28px 40px;
    display: flex;
    flex-direction: column;
    gap: 0;
  }

  .loading-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 32px 0;
    color: #555;
    font-size: 13px;
  }

  .spinner {
    width: 18px; height: 18px;
    border: 2px solid #222;
    border-top-color: #7c6cf4;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    flex-shrink: 0;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 60px 0;
    text-align: center;
  }
  .empty-icon { font-size: 32px; color: #222; margin-bottom: 4px; }
  .empty-state p { margin: 0; font-size: 14px; color: #666; }
  .empty-sub { font-size: 12px; color: #444 !important; max-width: 320px; }

  .list-meta {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
    font-size: 12px;
    color: #555;
  }
  .list-meta strong { color: #888; }

  .clear-link {
    background: none; border: none; color: #7c6cf4; cursor: pointer; font-size: 12px; padding: 0;
  }
  .clear-link:hover { text-decoration: underline; }

  .doc-item {
    padding: 16px 0;
    border-bottom: 1px solid #141414;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .doc-item:last-of-type { border-bottom: none; }
  .doc-item.expanded { background: #0d0d0d; padding: 16px 12px; border-radius: 6px; margin: 0 -12px; }

  .doc-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
  }

  .doc-title {
    font-size: 14px;
    font-weight: 500;
    color: #ddd;
    min-width: 0;
  }
  .doc-title a {
    color: #9d91f5;
    text-decoration: none;
  }
  .doc-title a:hover { text-decoration: underline; }

  .doc-meta {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-shrink: 0;
  }

  .source-type {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    color: #555;
    background: #151515;
    padding: 2px 5px;
    border-radius: 3px;
  }

  .doc-date { font-size: 11px; color: #444; }

  .expand-btn {
    background: #1a1a1a;
    border: 1px solid #2e2e2e;
    color: #666;
    width: 20px;
    height: 20px;
    border-radius: 4px;
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    padding: 0;
    transition: background 0.1s, color 0.1s;
  }
  .expand-btn:hover { background: #2a2552; border-color: #7c6cf4; color: #9d91f5; }

  .doc-fields {
    margin-top: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    border: 1px solid #1e1e1e;
    border-radius: 6px;
    padding: 12px;
    background: #0a0a0a;
  }

  .field-row {
    display: grid;
    grid-template-columns: 140px 1fr;
    gap: 12px;
    font-size: 12px;
    align-items: flex-start;
  }

  .field-key {
    color: #6b5ce7;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px;
    padding-top: 1px;
    flex-shrink: 0;
  }

  .field-val {
    color: #aaa;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.5;
  }

  /* Metadata parsed block */
  .meta-block {
    display: flex;
    flex-direction: column;
    gap: 4px;
    background: #0d0d10;
    border: 1px solid #1e1e2e;
    border-radius: 5px;
    padding: 8px 10px;
    width: 100%;
  }

  .meta-row {
    display: grid;
    grid-template-columns: 120px 1fr;
    gap: 10px;
    font-size: 11px;
    align-items: flex-start;
  }

  .meta-key {
    color: #7c6cf4;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 10px;
    padding-top: 1px;
    flex-shrink: 0;
    opacity: 0.85;
  }

  .meta-val {
    color: #bbb;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .meta-val.meta-price {
    color: #4cde93;
    font-weight: 600;
    font-size: 12px;
  }

  .meta-val.meta-price::before { content: '$'; }

  .meta-val.meta-stock {
    color: #4cde93;
  }

  .meta-images {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .meta-thumb {
    width: 52px;
    height: 52px;
    object-fit: contain;
    border-radius: 4px;
    background: #141414;
    border: 1px solid #222;
  }

  .field-vec {
    width: 100%;
    background: #0d0d0d;
    border: 1px solid #2a2a2a;
    border-radius: 4px;
    color: #666;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 10px;
    padding: 6px 8px;
    resize: none;
    overflow-y: auto;
    line-height: 1.5;
  }

  .doc-url {
    font-size: 11px;
    color: #3d3a6e;
    font-family: 'SF Mono', monospace;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .doc-snippet {
    font-size: 13px;
    color: #666;
    line-height: 1.55;
    margin-top: 2px;
  }

  .pagination {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 12px;
    color: #555;
    padding-top: 20px;
    justify-content: center;
  }
  .pagination button {
    background: #111;
    border: 1px solid #222;
    color: #777;
    border-radius: 6px;
    padding: 6px 14px;
    cursor: pointer;
    font-size: 12px;
  }
  .pagination button:disabled { opacity: 0.3; cursor: not-allowed; }
  .pagination button:not(:disabled):hover { border-color: #7c6cf4; color: #9d91f5; }
</style>
