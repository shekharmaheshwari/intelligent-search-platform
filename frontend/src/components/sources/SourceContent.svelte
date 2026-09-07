<script lang="ts">
  /**
   * SourceContent — main content area for a selected ISP index.
   *
   * Shows the source header (name, description, connector URL) and action buttons
   * (Extensions, Rebuild, Configure), then a searchable/paginated document list.
   *
   * Reactive to ``index`` prop changes — reloads the document list whenever
   * the selected index changes.  Dispatches ``configure`` when the user clicks
   * "Configure →" so App.svelte can navigate to the configure view.
   */
  import { onMount, onDestroy } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { documentsStore } from '../../lib/stores/documents-store';
  import type { IndexMeta, DocumentItem } from '../../lib/types';
  import RebuildPanel from './RebuildPanel.svelte';
  import IndexExtensionsPanel from '../extensions/IndexExtensionsPanel.svelte';
  import {
    excerpt, fmtDate, connectorUrl, docFields,
    tryParseJson, isMetadataKey, formatValue, formatMetaValue,
    getPopularity, getIsFeatured, getBoostQueries, applyEditToDoc,
  } from './SourceContent.helpers';

  export let index: IndexMeta;

  const dispatch = createEventDispatcher<{ configure: string }>();

  let showRebuild = false;
  let showExtensions = false;

  const perPage = 20;
  let page = 1;

  $: docs        = $documentsStore.docs;
  $: found       = $documentsStore.found;
  $: loadingDocs = $documentsStore.loading;

  let q = '';
  let searchMode: 'hybrid' | 'lexical' | 'semantic' = 'hybrid';
  const searchModes: Array<'hybrid' | 'lexical' | 'semantic'> = ['hybrid', 'lexical', 'semantic'];
  let searching = false;
  let searched = false;

  function loadDocs() {
    documentsStore.load(index.id, { page, perPage });
  }

  async function search() {
    if (!q.trim()) { searched = false; loadDocs(); return; }
    searching = true; searched = true;
    await documentsStore.search(index.id, q.trim(), { mode: searchMode, page, perPage: 10 });
    searching = false;
  }

  /** Submit a search on Enter key press in the search input. */
  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') search();
  }

  /** Clear the query, reset the searched flag, and reload the default document list. */
  function clearSearch() {
    q = '';
    searched = false;
    loadDocs(); // reload default list when user clears via ✕
  }

  /** Handle the Search button click — clear search when query is empty, otherwise run it. */
  function onSearchClick() {
    if (!q.trim()) { clearSearch(); return; }
    search();
  }

  let _loadedIndexId = '';
  $: if (index && index.id !== _loadedIndexId) {
    _loadedIndexId = index.id;
    page = 1; q = ''; searched = false;
    documentsStore.reset();
    loadDocs();
  }
  onDestroy(() => documentsStore.reset());

  let expandedIds = new Set<string>();
  function toggleExpand(id: string) {
    if (expandedIds.has(id)) expandedIds.delete(id);
    else expandedIds.add(id);
    expandedIds = expandedIds;
  }

  function prev() { if (page > 1) { page--; searched ? search() : loadDocs(); } }
  function next() { if (page * perPage < found) { page++; searched ? search() : loadDocs(); } }

  // ── Doc edit modal ────────────────────────────────────────────────────────────

  let editDoc: DocumentItem | null = null;
  let editPopularity = 0;
  let editIsFeatured = false;
  let editBoostEqual: string[] = [];
  let editBoostContains: string[] = [];
  let boostEqualInput = '';
  let boostContainsInput = '';
  let editSaving = false;
  let editErr = '';

  function openEdit(doc: DocumentItem) {
    editDoc = doc;
    editPopularity = getPopularity(doc);
    editIsFeatured = getIsFeatured(doc);
    editBoostEqual = getBoostQueries(doc, 'boost_queries_equal');
    editBoostContains = getBoostQueries(doc, 'boost_queries_contains');
    boostEqualInput = '';
    boostContainsInput = '';
    editErr = '';
  }

  function closeEdit() {
    editDoc = null; editErr = '';
    boostEqualInput = ''; boostContainsInput = '';
  }

  function addTag(arr: string[], input: string): { arr: string[]; input: string } {
    const v = input.trim().toLowerCase();
    if (v && !arr.includes(v)) arr = [...arr, v];
    return { arr, input: '' };
  }

  function onBoostEqualKey(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      const r = addTag(editBoostEqual, boostEqualInput);
      editBoostEqual = r.arr; boostEqualInput = r.input;
    }
  }

  function onBoostContainsKey(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      const r = addTag(editBoostContains, boostContainsInput);
      editBoostContains = r.arr; boostContainsInput = r.input;
    }
  }

  async function saveEdit() {
    if (!editDoc) return;
    // Flush any uncommitted tag-input text before saving
    if (boostEqualInput.trim()) {
      const r = addTag(editBoostEqual, boostEqualInput);
      editBoostEqual = r.arr; boostEqualInput = r.input;
    }
    if (boostContainsInput.trim()) {
      const r = addTag(editBoostContains, boostContainsInput);
      editBoostContains = r.arr; boostContainsInput = r.input;
    }
    editSaving = true; editErr = '';
    try {
      await documentsStore.updateDocument(
        index.id, editDoc.id,
        editPopularity, editIsFeatured,
        editBoostEqual, editBoostContains,
      );
      const target = editDoc;
      const patch = {
        popularity: editPopularity,
        isFeatured: editIsFeatured,
        boostEqual: editBoostEqual,
        boostContains: editBoostContains,
      };
      docs = docs.map(d => (d.id === target.id ? applyEditToDoc(d, patch) : d));
      closeEdit();
    } catch (e) {
      editErr = String(e);
    } finally {
      editSaving = false;
    }
  }
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
              {#if getIsFeatured(doc)}
                <span class="featured-badge">★ featured</span>
              {/if}
              <span class="score-badge" title="Popularity score">↑{getPopularity(doc)}</span>
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
              <div class="doc-edit-row">
                <button class="edit-doc-btn" on:click={() => openEdit(doc)}>✎ Edit</button>
              </div>
            </div>
          {/if}
        </div>
      {/each}

      <!-- Edit modal -->
      {#if editDoc}
        <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
        <div class="overlay" on:click|self={closeEdit} role="dialog" aria-modal="true">
          <div class="edit-modal">
            <div class="edit-modal-head">
              <h3>Edit document</h3>
              <button class="close-btn" on:click={closeEdit}>✕</button>
            </div>
            <p class="edit-modal-title">{editDoc.title || 'Untitled'}</p>
            <div class="edit-fields">
              <label class="edit-label">
                Score <span class="edit-hint">(higher = boosted in results)</span>
                <input
                  type="number"
                  class="edit-input"
                  bind:value={editPopularity}
                  min="0"
                  max="10000"
                />
              </label>
              <label class="edit-label edit-toggle-row">
                <span>Featured</span>
                <span class="edit-hint">Pin as a featured result</span>
                <input type="checkbox" class="edit-checkbox" bind:checked={editIsFeatured} />
                <span class="toggle-track" class:on={editIsFeatured}>
                  <span class="toggle-thumb"></span>
                </span>
              </label>

              <div class="edit-label">
                <span>Boost if query equals</span>
                <span class="edit-hint">+0.5 boost when search exactly matches — press Enter or , to add</span>
                <div class="tag-input-wrap">
                  {#each editBoostEqual as tag}
                    <span class="tag tag-equal">
                      {tag}
                      <button class="tag-remove" on:click={() => { editBoostEqual = editBoostEqual.filter(t => t !== tag); }}>×</button>
                    </span>
                  {/each}
                  <input
                    class="tag-input"
                    placeholder={editBoostEqual.length ? '' : 'e.g. iphone 16'}
                    bind:value={boostEqualInput}
                    on:keydown={onBoostEqualKey}
                  />
                </div>
              </div>

              <div class="edit-label">
                <span>Boost if query contains</span>
                <span class="edit-hint">+0.3 boost when search includes this word — press Enter or , to add</span>
                <div class="tag-input-wrap">
                  {#each editBoostContains as tag}
                    <span class="tag tag-contains">
                      {tag}
                      <button class="tag-remove" on:click={() => { editBoostContains = editBoostContains.filter(t => t !== tag); }}>×</button>
                    </span>
                  {/each}
                  <input
                    class="tag-input"
                    placeholder={editBoostContains.length ? '' : 'e.g. apple'}
                    bind:value={boostContainsInput}
                    on:keydown={onBoostContainsKey}
                  />
                </div>
              </div>
            </div>
            {#if editErr}<p class="edit-err">{editErr}</p>{/if}
            <div class="edit-actions">
              <button class="btn-secondary" on:click={closeEdit}>Cancel</button>
              <button class="btn-primary" on:click={saveEdit} disabled={editSaving}>
                {editSaving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      {/if}

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
    border-bottom: 1px solid var(--border);
    background: var(--bg);
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

  h1 { margin: 0; font-size: 20px; font-weight: 600; color: var(--text); }

  .source-desc { margin: 0; font-size: 13px; color: var(--text-4); }

  .source-url {
    font-size: 11px;
    color: var(--accent-border);
    font-family: 'SF Mono', monospace;
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    display: block;
    max-width: 100%;
  }
  .source-url:hover { color: var(--accent); text-decoration: underline; }

  .source-actions { display: flex; gap: 8px; align-items: center; flex-shrink: 0; flex-wrap: wrap; }

  .stat-pill {
    background: var(--bg-hover);
    border: 1px solid var(--border-2);
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 12px;
    color: var(--text-4);
  }

  .ext-btn {
    background: var(--bg-3);
    border: 1px solid var(--border-2);
    color: var(--text-4);
    border-radius: 7px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
  }
  .ext-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }

  .rebuild-btn {
    background: var(--accent-dim);
    border: 1px solid var(--accent-border);
    color: var(--accent);
    border-radius: 7px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
  }
  .rebuild-btn:hover { background: var(--accent-dim); border-color: var(--accent); opacity: 0.85; }

  .configure-btn {
    background: transparent;
    border: 1px solid var(--border-3);
    color: var(--text-3);
    border-radius: 7px;
    padding: 6px 14px;
    font-size: 12px;
    cursor: pointer;
    white-space: nowrap;
  }
  .configure-btn:hover { border-color: var(--accent); color: var(--accent); }

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
    background: var(--bg-3);
    border: 1px solid var(--border-2);
    border-radius: 8px;
    padding: 0 10px;
    gap: 8px;
  }
  .search-input-wrap:focus-within { border-color: var(--accent); }

  .search-icon { font-size: 16px; color: var(--text-6); }

  .search-input {
    flex: 1;
    background: none;
    border: none;
    color: var(--text);
    font-size: 13px;
    padding: 8px 0;
    outline: none;
  }
  .search-input::placeholder { color: var(--text-6); }

  .clear-btn {
    background: none;
    border: none;
    color: var(--text-6);
    cursor: pointer;
    font-size: 12px;
    padding: 0;
  }
  .clear-btn:hover { color: var(--text-3); }

  .search-btn {
    background: var(--accent);
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
  .search-btn:hover { background: var(--accent-hover); }

  .mode-toggle {
    display: flex;
    border: 1px solid var(--border-2);
    border-radius: 7px;
    overflow: hidden;
    flex-shrink: 0;
  }
  .mode-toggle button {
    background: transparent;
    border: none;
    color: var(--text-5);
    padding: 7px 12px;
    font-size: 11px;
    cursor: pointer;
    text-transform: capitalize;
    transition: background 0.1s;
  }
  .mode-toggle button.sel { background: var(--accent); color: #fff; }
  .mode-toggle button:hover:not(.sel) { color: var(--text-3); }

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
    color: var(--text-5);
    font-size: 13px;
  }

  .spinner {
    width: 18px; height: 18px;
    border: 2px solid var(--border-2);
    border-top-color: var(--accent);
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
  .empty-icon { font-size: 32px; color: var(--border-2); margin-bottom: 4px; }
  .empty-state p { margin: 0; font-size: 14px; color: var(--text-4); }
  .empty-sub { font-size: 12px; color: var(--text-6) !important; max-width: 320px; }

  .list-meta {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
    font-size: 12px;
    color: var(--text-5);
  }
  .list-meta strong { color: var(--text-3); }

  .clear-link {
    background: none; border: none; color: var(--accent); cursor: pointer; font-size: 12px; padding: 0;
  }
  .clear-link:hover { text-decoration: underline; }

  .doc-item {
    padding: 16px 0;
    border-bottom: 1px solid var(--bg-hover);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .doc-item:last-of-type { border-bottom: none; }
  .doc-item.expanded { background: var(--bg-3); padding: 16px 12px; border-radius: 6px; margin: 0 -12px; }

  .doc-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
  }

  .doc-title {
    font-size: 14px;
    font-weight: 500;
    color: var(--text-2);
    min-width: 0;
  }
  .doc-title a {
    color: var(--accent);
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
    color: var(--text-5);
    background: var(--bg-3);
    padding: 2px 5px;
    border-radius: 3px;
  }

  .doc-date { font-size: 11px; color: var(--text-6); }

  .expand-btn {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text-4);
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
  .expand-btn:hover { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }

  .doc-fields {
    margin-top: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px;
    background: var(--bg);
  }

  .field-row {
    display: grid;
    grid-template-columns: 140px 1fr;
    gap: 12px;
    font-size: 12px;
    align-items: flex-start;
  }

  .field-key {
    color: var(--accent-hover);
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px;
    padding-top: 1px;
    flex-shrink: 0;
  }

  .field-val {
    color: var(--text-3);
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.5;
  }

  .meta-block {
    display: flex;
    flex-direction: column;
    gap: 4px;
    background: var(--bg-3);
    border: 1px solid var(--border);
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
    color: var(--accent);
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 10px;
    padding-top: 1px;
    flex-shrink: 0;
    opacity: 0.85;
  }

  .meta-val {
    color: var(--text-2);
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
    background: var(--bg-hover);
    border: 1px solid var(--border-2);
  }

  .field-vec {
    width: 100%;
    background: var(--bg-3);
    border: 1px solid var(--border-3);
    border-radius: 4px;
    color: var(--text-4);
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 10px;
    padding: 6px 8px;
    resize: none;
    overflow-y: auto;
    line-height: 1.5;
  }

  .doc-url {
    font-size: 11px;
    color: var(--accent-border);
    font-family: 'SF Mono', monospace;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .featured-badge {
    font-size: 10px;
    font-weight: 700;
    background: #1a2a10;
    color: #7ecf55;
    border: 1px solid #2a4a1a;
    border-radius: 4px;
    padding: 2px 6px;
  }

  .score-badge {
    font-size: 10px;
    font-weight: 600;
    background: var(--bg-hover);
    color: var(--text-5);
    border: 1px solid var(--border-2);
    border-radius: 4px;
    padding: 2px 5px;
    font-family: 'SF Mono', monospace;
  }

  .doc-edit-row {
    display: flex;
    justify-content: flex-end;
    padding-top: 8px;
    border-top: 1px solid var(--border);
    margin-top: 4px;
  }

  .edit-doc-btn {
    background: var(--accent-dim);
    border: 1px solid var(--accent-border);
    color: var(--accent);
    border-radius: 6px;
    padding: 5px 14px;
    font-size: 12px;
    cursor: pointer;
    transition: background 0.15s;
  }
  .edit-doc-btn:hover { opacity: 0.8; }

  .edit-modal {
    background: var(--bg-hover);
    border: 1px solid var(--border-3);
    border-radius: 12px;
    width: 400px;
    max-width: 95vw;
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .edit-modal-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .edit-modal-head h3 { margin: 0; font-size: 15px; color: var(--text); font-weight: 600; }

  .close-btn {
    background: none; border: none; color: var(--text-5); cursor: pointer; font-size: 15px;
  }
  .close-btn:hover { color: var(--text-3); }

  .edit-modal-title {
    margin: 0;
    font-size: 12px;
    color: var(--text-4);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .edit-fields { display: flex; flex-direction: column; gap: 14px; }

  .edit-label {
    display: flex;
    flex-direction: column;
    gap: 5px;
    font-size: 13px;
    color: var(--text-3);
  }

  .edit-hint { font-size: 11px; color: var(--text-5); }

  .edit-input {
    background: var(--bg-3);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 10px;
    color: var(--text);
    font-size: 14px;
    font-family: inherit;
    width: 100%;
  }
  .edit-input:focus { outline: none; border-color: var(--accent); }

  .edit-toggle-row {
    flex-direction: row !important;
    align-items: center;
    gap: 10px !important;
    flex-wrap: wrap;
  }
  .edit-toggle-row span:first-child { font-weight: 500; color: var(--text-2); }
  .edit-toggle-row .edit-hint { flex: 1; }
  .edit-checkbox { display: none; }

  .toggle-track {
    width: 36px; height: 20px;
    background: var(--border-3);
    border: 1px solid var(--border-3);
    border-radius: 10px;
    cursor: pointer;
    position: relative;
    flex-shrink: 0;
    transition: background 0.2s, border-color 0.2s;
  }
  .toggle-track.on { background: var(--accent); border-color: var(--accent); }

  .toggle-thumb {
    position: absolute;
    top: 2px; left: 2px;
    width: 14px; height: 14px;
    background: var(--text-3);
    border-radius: 50%;
    transition: transform 0.2s, background 0.2s;
  }
  .toggle-track.on .toggle-thumb { transform: translateX(16px); background: #fff; }

  .edit-err { margin: 0; font-size: 12px; color: var(--danger-text); }

  .tag-input-wrap {
    display: flex; flex-wrap: wrap; gap: 5px; align-items: center;
    background: var(--bg-3); border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 8px; min-height: 36px; cursor: text;
  }
  .tag-input-wrap:focus-within { border-color: var(--accent); }
  .tag {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 11px; font-weight: 600; border-radius: 4px; padding: 2px 6px;
  }
  .tag-equal   { background: #1e2a40; color: #70a8ff; }
  .tag-contains { background: #1e2a1e; color: #5cdb85; }
  .tag-remove {
    background: none; border: none; cursor: pointer; font-size: 13px;
    line-height: 1; padding: 0; opacity: 0.6; color: inherit;
  }
  .tag-remove:hover { opacity: 1; }
  .tag-input {
    flex: 1; min-width: 80px; background: none; border: none;
    color: var(--text); font-size: 12px; outline: none; padding: 0;
  }
  .tag-input::placeholder { color: var(--text-6); }

  .edit-actions {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
  }

  .btn-primary {
    background: var(--accent); color: #fff; border: none;
    border-radius: 7px; padding: 8px 18px; font-size: 13px; font-weight: 600; cursor: pointer;
  }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

  .btn-secondary {
    background: transparent; color: var(--text-3); border: 1px solid var(--border-2);
    border-radius: 7px; padding: 8px 18px; font-size: 13px; cursor: pointer;
  }
  .btn-secondary:hover { border-color: var(--text-5); color: var(--text-2); }

  .doc-snippet {
    font-size: 13px;
    color: var(--text-4);
    line-height: 1.55;
    margin-top: 2px;
  }

  .pagination {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 12px;
    color: var(--text-5);
    padding-top: 20px;
    justify-content: center;
  }
  .pagination button {
    background: var(--bg-3);
    border: 1px solid var(--border-2);
    color: var(--text-4);
    border-radius: 6px;
    padding: 6px 14px;
    cursor: pointer;
    font-size: 12px;
  }
  .pagination button:disabled { opacity: 0.3; cursor: not-allowed; }
  .pagination button:not(:disabled):hover { border-color: var(--accent); color: var(--accent); }
</style>
