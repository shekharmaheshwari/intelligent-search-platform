<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  /**
   * SearchPanel — standalone search UI used inside the IndexDetail configure view.
   *
   * Renders a query input, hybrid/lexical/semantic mode toggle, and result cards.
   * Simpler than the SourceContent search bar — no pagination, just the first page.
   */
  import { indexService } from '../../lib/services/indexes/IndexService';
  import type { DocumentItem } from '../../lib/types';
  import { excerpt } from './SearchPanel.helpers';

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
      const res = await indexService.search(indexId, q.trim(), mode);
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
    background: var(--bg-2);
    border: 1px solid var(--border-2);
    border-radius: 7px;
    padding: 9px 12px;
    color: var(--text);
    font-size: 14px;
  }
  .search-input:focus { outline: none; border-color: var(--accent); }
  .mode-toggle {
    display: flex;
    border: 1px solid var(--border-2);
    border-radius: 7px;
    overflow: hidden;
  }
  .mode-toggle button {
    background: transparent;
    border: none;
    color: var(--text-4);
    padding: 7px 12px;
    font-size: 12px;
    cursor: pointer;
    text-transform: capitalize;
  }
  .mode-toggle button.sel { background: var(--accent); color: #fff; }
  .btn-search {
    background: var(--accent);
    border: none;
    border-radius: 7px;
    color: #fff;
    padding: 9px 18px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .btn-search:disabled { opacity: 0.5; cursor: not-allowed; }
  .empty { color: var(--text-5); font-size: 13px; text-align: center; padding: 32px 0; }
  .err { color: var(--danger-text); font-size: 12px; }
  .result-meta { font-size: 12px; color: var(--text-5); }
  .results { display: flex; flex-direction: column; gap: 14px; }
  .result {
    background: var(--bg-3);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
  }
  .result-title a {
    color: var(--accent);
    text-decoration: none;
    font-size: 14px;
    font-weight: 500;
  }
  .result-title a:hover { text-decoration: underline; }
  .result-title span { color: var(--text-2); font-size: 14px; font-weight: 500; }
  .result-url { font-size: 11px; color: var(--text-6); margin: 3px 0 6px; }
  .result-snippet { font-size: 13px; color: var(--text-4); line-height: 1.5; }
</style>
