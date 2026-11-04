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
   * The optional AI Overview toggle (shown when enabled in Settings → Chat Model)
   * mounts <AIOverview> above the results; search itself never waits on it.
   */
  import { onMount } from 'svelte';
  import { api } from '../../lib/api';
  import { indexService } from '../../lib/services/indexes/IndexService';
  import type { DocumentItem } from '../../lib/types';
  import { excerpt } from './SearchPanel.helpers';
  import AIOverview from './AIOverview.svelte';

  export let indexId: string;

  let q = '';
  let mode: 'hybrid' | 'lexical' | 'semantic' = 'hybrid';
  const modes: Array<'hybrid' | 'lexical' | 'semantic'> = ['hybrid', 'lexical', 'semantic'];
  let results: DocumentItem[] = [];
  let found = 0;
  let loading = false;
  let searched = false;
  let errMsg = '';

  const OVERVIEW_PREF_KEY = 'isp-search-ai-overview';
  let overviewAvailable = false;   // Settings switch on + chat model configured
  let overviewOn = false;          // this viewer's toggle, remembered locally
  let overviewQuery = '';          // set only on a completed search, so typing never triggers it
  let overviewIds: string[] = [];

  onMount(async () => {
    try { overviewOn = localStorage.getItem(OVERVIEW_PREF_KEY) === '1'; } catch { /* storage blocked */ }
    try {
      const cfg = await api.getChatConfig();
      overviewAvailable = cfg.ai_overview && cfg.llm_response && cfg.provider !== 'none';
    } catch { /* chat settings unavailable — leave the toggle hidden */ }
  });

  function toggleOverview() {
    overviewOn = !overviewOn;
    try { localStorage.setItem(OVERVIEW_PREF_KEY, overviewOn ? '1' : '0'); } catch { /* storage blocked */ }
  }

  let searchSeq = 0;

  async function search() {
    // Capture the submitted query: the user may keep typing while the search runs,
    // and the overview must describe the query these results are for.
    const submitted = q.trim();
    if (!submitted) return;
    const seq = ++searchSeq;
    loading = true; errMsg = ''; searched = true;
    try {
      const res = await indexService.search(indexId, submitted, mode);
      if (seq !== searchSeq) return;   // a newer search started — its results win
      results = res.hits.map(h => h.document);
      found = res.found;
      overviewQuery = submitted;
      overviewIds = results.map(d => String(d.id));
    } catch (e) {
      if (seq === searchSeq) errMsg = String(e);
    } finally {
      if (seq === searchSeq) loading = false;
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
    {#if overviewAvailable}
      <button class="ai-toggle" class:sel={overviewOn} on:click={toggleOverview}
        aria-pressed={overviewOn} title="Show an AI summary of the top results">✦ AI Overview</button>
    {/if}
    <button class="btn-search" on:click={search} disabled={loading}>Search</button>
  </div>

  {#if errMsg}
    <p class="err">{errMsg}</p>
  {:else if loading}
    <div class="empty">Searching…</div>
  {:else if searched && results.length === 0}
    <div class="empty">No results for "{q}"</div>
  {:else if results.length > 0}
    {#if overviewAvailable && overviewOn}
      <AIOverview {indexId} query={overviewQuery} docIds={overviewIds} />
    {/if}
    <div class="result-meta">{found.toLocaleString()} results</div>
    <div class="results">
      {#each results as doc}
        <div class="result" id="result-{doc.id}">
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
  .ai-toggle {
    background: transparent;
    border: 1px solid var(--border-2);
    border-radius: 7px;
    color: var(--text-4);
    padding: 7px 12px;
    font-size: 12px;
    cursor: pointer;
  }
  .ai-toggle.sel { border-color: var(--accent); color: var(--accent); background: var(--bg-3); }
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
  .result { transition: box-shadow 0.3s; }
  :global(.result.cited) { box-shadow: 0 0 0 2px var(--accent); }
  .result-snippet { font-size: 13px; color: var(--text-4); line-height: 1.5; }
</style>
