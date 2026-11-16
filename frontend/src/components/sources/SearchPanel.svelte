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
   *
   * When image search is on for the index, a photo can be attached (camera
   * button, paste, or drag-and-drop). It is downsized in the browser and sent
   * with any typed text, which refines it (e.g. "in black"); results then rank
   * by similarity to the product images and show a match score.
   */
  import { onMount } from 'svelte';
  import { api } from '../../lib/api';
  import { indexService } from '../../lib/services/indexes/IndexService';
  import type { DocumentItem, ImageSearchState } from '../../lib/types';
  import { excerpt } from './SearchPanel.helpers';
  import {
    validateImageFile, fileToSearchDataUrl, imageFromTransfer, docImageUrl, matchPercent, apiDetail,
  } from './imageSearch.helpers';
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

  // ── Image search ──
  let imageCfg: ImageSearchState | null = null;
  let attached: { dataUrl: string; name: string } | null = null;
  let distances: Record<string, number> = {};
  let imageResults = false;      // the shown results came from an image search
  let dragOver = false;
  let fileInput: HTMLInputElement;
  $: imageEnabled = !!imageCfg?.enabled && !!imageCfg?.model;
  $: imageField = imageCfg?.field ?? '';

  const OVERVIEW_PREF_KEY = 'isp-search-ai-overview';
  let overviewAvailable = false;   // Settings switch on + chat model configured
  let overviewOn = false;          // this viewer's toggle, remembered locally
  let overviewQuery = '';          // set only on a completed search, so typing never triggers it
  let overviewIds: string[] = [];

  onMount(async () => {
    try { overviewOn = localStorage.getItem(OVERVIEW_PREF_KEY) === '1'; } catch { /* storage blocked */ }
    // Not available for external indexes or when off — the camera button stays hidden.
    indexService.getImageSearch(indexId).then(c => (imageCfg = c)).catch(() => (imageCfg = null));
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
    const image = attached?.dataUrl ?? '';
    if (!submitted && !image) return;
    const seq = ++searchSeq;
    loading = true; errMsg = ''; searched = true;
    try {
      if (image) {
        const res = await indexService.searchByImage(indexId, image, submitted);
        if (seq !== searchSeq) return;
        results = res.hits.map(h => h.document);
        distances = Object.fromEntries(res.hits.map(h => [String(h.document.id), h.vector_distance]));
        found = res.found;
        imageResults = true;
        overviewQuery = '';           // the overview summarises text queries only
      } else {
        const res = await indexService.search(indexId, submitted, mode);
        if (seq !== searchSeq) return;   // a newer search started — its results win
        results = res.hits.map(h => h.document);
        distances = {};
        found = res.found;
        imageResults = false;
        overviewQuery = submitted;
      }
      overviewIds = results.map(d => String(d.id));
    } catch (e) {
      if (seq === searchSeq) errMsg = apiDetail(e);
    } finally {
      if (seq === searchSeq) loading = false;
    }
  }

  async function attach(file: File | null) {
    if (!file) return;
    const problem = validateImageFile(file);
    if (problem) { errMsg = problem; return; }
    try {
      attached = { dataUrl: await fileToSearchDataUrl(file), name: file.name || 'pasted image' };
      errMsg = '';
      search();
    } catch {
      errMsg = 'Could not read that image';
    }
  }

  function onPaste(e: ClipboardEvent) {
    if (!imageEnabled) return;
    const file = imageFromTransfer(e.clipboardData);
    if (file) { e.preventDefault(); attach(file); }
  }

  function onDrop(e: DragEvent) {
    dragOver = false;
    if (!imageEnabled) return;
    const file = imageFromTransfer(e.dataTransfer);
    if (file) { e.preventDefault(); attach(file); }
  }

  function clearImage() {
    attached = null;
    if (fileInput) fileInput.value = '';
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') search();
  }
</script>

<div class="panel">
  <!-- svelte-ignore a11y-no-static-element-interactions -->
  <div
    class="search-row"
    class:drag-over={dragOver}
    on:dragover={(e) => { if (imageEnabled) { e.preventDefault(); dragOver = true; } }}
    on:dragleave={() => (dragOver = false)}
    on:drop={onDrop}
  >
    <div class="search-box">
      {#if attached}
        <span class="image-chip" title={attached.name}>
          <img src={attached.dataUrl} alt="Search image" />
          <button on:click={clearImage} aria-label="Remove image">✕</button>
        </span>
      {/if}
      <input
        bind:value={q}
        on:keydown={onKeydown}
        on:paste={onPaste}
        placeholder={attached ? 'Optional: refine, e.g. “in black”' : imageEnabled ? 'Search documents, or paste / drop a photo…' : 'Search documents…'}
        class="search-input"
      />
      {#if imageEnabled}
        <button class="camera" on:click={() => fileInput.click()} title="Search with an image" aria-label="Search with an image">
          <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
            <path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"
              d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z" />
            <circle cx="12" cy="13" r="3.5" fill="none" stroke="currentColor" stroke-width="1.8" />
          </svg>
        </button>
        <input
          bind:this={fileInput}
          type="file"
          accept="image/jpeg,image/png,image/webp,image/gif,image/bmp"
          capture="environment"
          hidden
          on:change={(e) => attach(e.currentTarget.files?.[0] ?? null)}
        />
      {/if}
    </div>
    {#if !attached}
      <div class="mode-toggle">
        {#each modes as m}
          <button class:sel={mode === m} on:click={() => mode = m}>{m}</button>
        {/each}
      </div>
    {/if}
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
    <div class="empty">{imageResults ? 'No similar products found' : `No results for "${q}"`}</div>
  {:else if results.length > 0}
    {#if overviewAvailable && overviewOn && overviewQuery}
      <AIOverview {indexId} query={overviewQuery} docIds={overviewIds} />
    {/if}
    <div class="result-meta">
      {found.toLocaleString()} results{#if imageResults} · ranked by visual similarity{/if}
    </div>
    <div class="results">
      {#each results as doc}
        {@const thumb = imageField ? docImageUrl(doc, imageField) : null}
        <div class="result" class:with-thumb={!!thumb} id="result-{doc.id}">
          {#if thumb}
            <img class="thumb" src={thumb} alt="" loading="lazy" referrerpolicy="no-referrer" />
          {/if}
          <div class="result-body">
          {#if imageResults && distances[String(doc.id)] !== undefined}
            <span class="match">{matchPercent(distances[String(doc.id)])}% match</span>
          {/if}
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
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .panel { display: flex; flex-direction: column; gap: 16px; }
  .search-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; border-radius: 9px; }
  .search-row.drag-over { outline: 2px dashed var(--accent); outline-offset: 4px; }
  .search-box {
    flex: 1;
    min-width: 200px;
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--bg-2);
    border: 1px solid var(--border-2);
    border-radius: 7px;
    padding: 0 6px 0 0;
  }
  .search-box:focus-within { border-color: var(--accent); }
  .search-input {
    flex: 1;
    min-width: 0;
    background: transparent;
    border: none;
    padding: 9px 12px;
    color: var(--text);
    font-size: 14px;
  }
  .search-input:focus { outline: none; }
  .image-chip {
    display: inline-flex; align-items: center; gap: 2px; margin-left: 6px;
    background: var(--bg-3); border: 1px solid var(--border-2); border-radius: 6px; padding: 2px;
  }
  .image-chip img { width: 28px; height: 28px; object-fit: cover; border-radius: 4px; display: block; }
  .image-chip button {
    background: none; border: none; color: var(--text-5); cursor: pointer; font-size: 11px; padding: 2px 4px;
  }
  .image-chip button:hover { color: var(--text-2); }
  .camera {
    background: none; border: none; color: var(--text-5); cursor: pointer;
    padding: 6px; border-radius: 5px; display: inline-flex;
  }
  .camera:hover { color: var(--accent); background: var(--bg-3); }
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
  .result.with-thumb { display: flex; gap: 14px; align-items: flex-start; }
  .result-body { min-width: 0; flex: 1; }
  .thumb {
    width: 64px; height: 64px; object-fit: cover; border-radius: 6px; flex-shrink: 0;
    background: var(--bg-2); border: 1px solid var(--border);
  }
  .match {
    float: right; font-size: 11px; font-weight: 600; color: var(--accent);
    background: var(--accent-dim); border-radius: 10px; padding: 2px 8px; margin-left: 8px;
  }
</style>
