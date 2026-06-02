<script lang="ts">
  import { api } from '../lib/api';
  import type { DocumentItem } from '../lib/types';

  export let indexId: string;

  let q = '';
  let mode: 'hybrid' | 'lexical' | 'semantic' = 'hybrid';
  const modes: Array<'hybrid' | 'lexical' | 'semantic'> = ['hybrid', 'lexical', 'semantic'];
  let results: DocumentItem[] = [];
  let found = 0;
  let loading = false;
  let searched = false;
  let errMsg = '';

  async function search() {
    if (!q.trim()) return;
    loading = true; errMsg = ''; searched = true;
    try {
      const res = await api.search(indexId, q.trim(), mode);
      results = res.hits.map(h => h.document);
      found = res.found;
    } catch (e) {
      errMsg = String(e);
    } finally {
      loading = false;
    }
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') search();
  }

  function excerpt(text: string, max = 160) {
    return text.length > max ? text.slice(0, max) + '…' : text;
  }
</script>

<div class="panel">
  <div class="search-row">
    <input
      bind:value={q}
      on:keydown={onKeydown}
      placeholder="Search documents…"
      class="search-input"
    />
    <div class="mode-toggle">
      {#each modes as m}
        <button class:sel={mode === m} on:click={() => mode = m}>{m}</button>
      {/each}
    </div>
    <button class="btn-search" on:click={search} disabled={loading}>Search</button>
  </div>

  {#if errMsg}
    <p class="err">{errMsg}</p>
  {:else if loading}
    <div class="empty">Searching…</div>
  {:else if searched && results.length === 0}
    <div class="empty">No results for "{q}"</div>
  {:else if results.length > 0}
    <div class="result-meta">{found.toLocaleString()} results</div>
    <div class="results">
      {#each results as doc}
        <div class="result">
          <div class="result-title">
            {#if doc.url}
              <a href={doc.url} target="_blank" rel="noopener">{doc.title || doc.url}</a>
            {:else}
              <span>{doc.title || 'Untitled'}</span>
            {/if}
          </div>
          {#if doc.url}
            <div class="result-url">{doc.url}</div>
          {/if}
          <div class="result-snippet">{excerpt(doc.content)}</div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .panel { display: flex; flex-direction: column; gap: 16px; }
  .search-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .search-input {
    flex: 1;
    min-width: 200px;
    background: #1a1a1a;
    border: 1px solid #2e2e2e;
    border-radius: 7px;
    padding: 9px 12px;
    color: #eee;
    font-size: 14px;
  }
  .search-input:focus { outline: none; border-color: #7c6cf4; }
  .mode-toggle {
    display: flex;
    border: 1px solid #2e2e2e;
    border-radius: 7px;
    overflow: hidden;
  }
  .mode-toggle button {
    background: transparent;
    border: none;
    color: #666;
    padding: 7px 12px;
    font-size: 12px;
    cursor: pointer;
    text-transform: capitalize;
  }
  .mode-toggle button.sel { background: #7c6cf4; color: #fff; }
  .btn-search {
    background: #7c6cf4;
    border: none;
    border-radius: 7px;
    color: #fff;
    padding: 9px 18px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .btn-search:disabled { opacity: 0.5; cursor: not-allowed; }
  .empty { color: #555; font-size: 13px; text-align: center; padding: 32px 0; }
  .err { color: #e55; font-size: 12px; }
  .result-meta { font-size: 12px; color: #555; }
  .results { display: flex; flex-direction: column; gap: 14px; }
  .result {
    background: #111;
    border: 1px solid #1e1e1e;
    border-radius: 8px;
    padding: 14px 16px;
  }
  .result-title a {
    color: #9d91f5;
    text-decoration: none;
    font-size: 14px;
    font-weight: 500;
  }
  .result-title a:hover { text-decoration: underline; }
  .result-title span { color: #ccc; font-size: 14px; font-weight: 500; }
  .result-url { font-size: 11px; color: #444; margin: 3px 0 6px; }
  .result-snippet { font-size: 13px; color: #777; line-height: 1.5; }
</style>
