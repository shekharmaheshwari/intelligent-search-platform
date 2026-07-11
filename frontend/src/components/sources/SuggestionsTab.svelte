<script lang="ts">
  // SuggestionsTab — paginated, searchable list of autocomplete suggestions for
  // an index, grouped into PROMOTED / ORGANIC / SUPPRESSED tiers.
  // Reads data via suggestionsService (transient, not shared globally).

  import { onMount } from 'svelte';
  import { suggestionsService } from '../../lib/services/suggestions/SuggestionsService';
  import DocPickerModal from './DocPickerModal.svelte';
  import type { Suggestion } from '../../lib/types';

  /** Id of the index whose suggestions this tab manages. */
  export let indexId: string;

  /** Pre-fill for the category field in the Add modal; derived from the index intent label. */
  export let category: string | undefined;

  // ── Data state ───────────────────────────────────────────────────────────────

  let suggestions: Suggestion[] = [];
  let total = 0;
  let page = 1;
  const perPage = 20;
  let loading = false;
  let error = '';

  // ── Search state ─────────────────────────────────────────────────────────────

  let q = '';
  /** Timer id for the debounce; reset on every keystroke. */
  let debounceTimer: ReturnType<typeof setTimeout>;

  // ── Tier-grouped reactive derivations ────────────────────────────────────────

  // Split the flat suggestions array into the three display tiers.
  $: promoted   = suggestions.filter(s => s.boost ===  1);
  $: organic    = suggestions.filter(s => s.boost ===  0);
  $: suppressed = suggestions.filter(s => s.boost === -1);

  // ── Edit modal state ─────────────────────────────────────────────────────────

  let editModal    = false;
  let editingItem: Suggestion | null = null;
  let editBoost    = 0;
  let editSaving   = false;
  let editErr      = '';
  /** Two-step delete: first click reveals confirmation; second click fires the request. */
  let confirmDelete = false;
  /** "link" navigates directly to editUrl; "search" runs the query through ISP. */
  let editMode: 'link' | 'search' = 'search';
  /** Destination URL, populated when editMode is "link". */
  let editUrl = '';
  /** Document IDs to pin at the top positions when editMode is "search". */
  let editFeaturedDocIds: string[] = [];
  /** Staging input for adding another doc ID to the list. */
  let editFeaturedDocIdInput = '';

  // ── Add modal state ──────────────────────────────────────────────────────────

  let addModal    = false;
  let addQuery    = '';
  let addCategory = category ?? '';
  let addBoost    = 1; // default to Promote so manual additions are immediately visible
  let addSaving   = false;
  let addErr      = '';

  // ── Data loading ─────────────────────────────────────────────────────────────

  /** Fetch the current page of suggestions, respecting the active search query. */
  async function load(): Promise<void> {
    if (!category) return;
    loading = true;
    error = '';
    try {
      const res = await suggestionsService.listSuggestions(indexId, {
        // Omit q entirely when blank so the backend returns all suggestions.
        q: q.trim() || undefined,
        page,
        per_page: perPage,
      });
      suggestions = res.suggestions;
      total       = res.total;
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  /** Handle search input with a 300 ms debounce to avoid hammering the API. */
  function onSearchInput(): void {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      page = 1;
      load();
    }, 300);
  }

  // ── Pagination ───────────────────────────────────────────────────────────────

  function prev(): void { if (page > 1) { page--; load(); } }
  function next(): void { if (page * perPage < total) { page++; load(); } }

  // ── Edit modal ───────────────────────────────────────────────────────────────

  /** Open the edit modal pre-filled with the suggestion's current values. */
  function openEdit(s: Suggestion): void {
    editingItem       = s;
    editBoost         = s.boost;
    // Default to 'search' when the field is absent so existing rows behave correctly.
    editMode             = (s.mode ?? 'search') as 'link' | 'search';
    editUrl              = s.url ?? '';
    editFeaturedDocIds   = s.featured_doc_ids ?? [];
    editFeaturedDocIdInput = '';
    editErr           = '';
    confirmDelete     = false;
    editModal         = true;
  }

  /** Close the edit modal and reset all edit state. */
  function closeEdit(): void {
    editModal         = false;
    editingItem       = null;
    editErr           = '';
    confirmDelete     = false;
    editMode               = 'search';
    editUrl                = '';
    editFeaturedDocIds     = [];
    editFeaturedDocIdInput = '';
  }

  function addFeaturedDocId(): void {
    const id = editFeaturedDocIdInput.trim();
    if (id && !editFeaturedDocIds.includes(id)) {
      editFeaturedDocIds = [...editFeaturedDocIds, id];
    }
    editFeaturedDocIdInput = '';
  }

  function removeFeaturedDocId(id: string): void {
    editFeaturedDocIds = editFeaturedDocIds.filter(d => d !== id);
  }

  let showDocPicker = false;

  function selectPickerDoc(doc: { id: string; title: string }): void {
    if (!editFeaturedDocIds.includes(doc.id)) {
      editFeaturedDocIds = [...editFeaturedDocIds, doc.id];
    }
    showDocPicker = false;
  }

  /** Persist boost, mode, url, and featured_doc_ids together and reload the list. */
  async function saveEdit(): Promise<void> {
    if (!editingItem) return;
    editSaving = true;
    editErr    = '';
    try {
      await suggestionsService.updateSuggestion(indexId, editingItem.id, {
        boost:            editBoost,
        mode:             editMode,
        // Only send url when mode is "link"; omit it entirely for search mode.
        url:              editMode === 'link' ? editUrl.trim() || undefined : undefined,
        // Only send featured_doc_ids when mode is "search"; omit for link mode.
        featured_doc_ids: editMode === 'search' ? editFeaturedDocIds : undefined,
      });
      await load();
      closeEdit();
    } catch (e) {
      editErr = String(e);
    } finally {
      editSaving = false;
    }
  }

  /** First step of the two-step delete: show the confirmation UI. */
  function startDelete(): void {
    confirmDelete = true;
  }

  /** Second step of the two-step delete: send the API request. */
  async function doDelete(): Promise<void> {
    if (!editingItem) return;
    editSaving = true;
    editErr    = '';
    try {
      await suggestionsService.deleteSuggestion(indexId, editingItem.id);
      await load();
      closeEdit();
    } catch (e) {
      editErr = String(e);
    } finally {
      editSaving = false;
    }
  }

  // ── Add modal ────────────────────────────────────────────────────────────────

  /** Open the add modal, resetting all fields and pre-filling category from the index. */
  function openAdd(): void {
    addQuery    = '';
    addCategory = category ?? '';
    addBoost    = 1;
    addErr      = '';
    addModal    = true;
  }

  /** Close the add modal and reset state. */
  function closeAdd(): void {
    addModal = false;
    addErr   = '';
  }

  /** Validate, create the suggestion, reload the list, and close the modal. */
  async function submitAdd(): Promise<void> {
    if (!addQuery.trim()) return;
    addSaving = true;
    addErr    = '';
    try {
      await suggestionsService.createSuggestion(indexId, {
        query:    addQuery.trim(),
        boost:    addBoost,
        // Omit category entirely when blank — the backend treats absence as no category.
        category: addCategory.trim() || undefined,
      });
      await load();
      closeAdd();
    } catch (e) {
      addErr = String(e);
    } finally {
      addSaving = false;
    }
  }
</script>

<!-- ── Main panel ──────────────────────────────────────────────────────────── -->
<div class="suggestions-panel">

  <!-- Toolbar: search + count + add button -->
  <div class="toolbar">
    <input
      class="search-input"
      type="search"
      placeholder="Filter suggestions…"
      bind:value={q}
      on:input={onSearchInput}
    />
    <span class="count">{total.toLocaleString()} suggestion{total === 1 ? '' : 's'}</span>
    <button class="btn-refresh" on:click={load} title="Refresh">↻</button>
    <button class="btn-add" on:click={openAdd}>+ Add suggestion</button>
  </div>

  {#if !category}
    <div class="empty no-label-notice">
      No category configured for this index.<br/>
      Set an <strong>intent label</strong> on the index to enable scoped suggestions.
    </div>

  {:else if error}
    <div class="error-banner">{error}</div>

  {:else if loading}
    <div class="empty">Loading…</div>

  {:else if suggestions.length === 0}
    <div class="empty">No suggestions yet.</div>

  {:else}
    <!-- ── Tier sections ──────────────────────────────────────────────────── -->

    {#if promoted.length > 0}
      <div class="tier-section">
        <div class="tier-header tier-promoted">
          <span class="tier-label">PROMOTED</span>
          <span class="tier-count">{promoted.length}</span>
        </div>
        {#each promoted as s (s.id)}
          <div class="suggestion-row">
            <span class="query-text">{s.query}</span>
            {#if s.category}
              <span class="badge badge-cat">{s.category}</span>
            {/if}
            {#if s.mode === 'link'}
              <span class="badge badge-link">↗ link</span>
            {/if}
            <span class="pop">{s.popularity.toLocaleString()} searches</span>
            <span class="badge badge-type">{s.type}</span>
            <button class="btn-edit" on:click={() => openEdit(s)}>Edit</button>
          </div>
        {/each}
      </div>
    {/if}

    <!-- ORGANIC is always shown so there is always a landing section. -->
    <div class="tier-section">
      <div class="tier-header tier-organic">
        <span class="tier-label">ORGANIC</span>
        <span class="tier-count">{organic.length}</span>
      </div>
      {#if organic.length === 0}
        <div class="tier-empty">No organic suggestions on this page.</div>
      {:else}
        {#each organic as s (s.id)}
          <div class="suggestion-row">
            <span class="query-text">{s.query}</span>
            {#if s.category}
              <span class="badge badge-cat">{s.category}</span>
            {/if}
            {#if s.mode === 'link'}
              <span class="badge badge-link">↗ link</span>
            {/if}
            <span class="pop">{s.popularity.toLocaleString()} searches</span>
            <span class="badge badge-type">{s.type}</span>
            <button class="btn-edit" on:click={() => openEdit(s)}>Edit</button>
          </div>
        {/each}
      {/if}
    </div>

    {#if suppressed.length > 0}
      <div class="tier-section">
        <div class="tier-header tier-suppressed">
          <span class="tier-label">SUPPRESSED</span>
          <span class="tier-count">{suppressed.length}</span>
        </div>
        {#each suppressed as s (s.id)}
          <div class="suggestion-row suggestion-row-suppressed">
            <span class="query-text">{s.query}</span>
            {#if s.category}
              <span class="badge badge-cat">{s.category}</span>
            {/if}
            {#if s.mode === 'link'}
              <span class="badge badge-link">↗ link</span>
            {/if}
            <span class="pop">{s.popularity.toLocaleString()} searches</span>
            <span class="badge badge-type">{s.type}</span>
            <button class="btn-edit" on:click={() => openEdit(s)}>Edit</button>
          </div>
        {/each}
      </div>
    {/if}

    <!-- Pagination -->
    <div class="pagination">
      <button on:click={prev} disabled={page === 1}>← Prev</button>
      <span>Page {page}</span>
      <button on:click={next} disabled={page * perPage >= total}>Next →</button>
    </div>
  {/if}
</div>

<!-- ── Edit modal ──────────────────────────────────────────────────────────── -->
{#if editModal && editingItem}
  <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
  <div class="overlay" on:click|self={closeEdit}>
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-header">
        <h3>Edit suggestion</h3>
        <button class="btn-close" on:click={closeEdit}>✕</button>
      </div>

      <!-- Read-only fields -->
      <div class="modal-field">
        <span class="modal-label">Query</span>
        <span class="modal-val">{editingItem.query}</span>
      </div>
      {#if editingItem.category}
        <div class="modal-field">
          <span class="modal-label">Category</span>
          <span class="modal-val">{editingItem.category}</span>
        </div>
      {/if}
      <div class="modal-field">
        <span class="modal-label">Popularity</span>
        <span class="modal-val">{editingItem.popularity.toLocaleString()} searches</span>
      </div>

      <!-- Mode toggle: "link" navigates to a URL; "search" runs ISP search -->
      <div class="modal-field modal-field-col">
        <span class="modal-label">Mode</span>
        <div class="mode-radios">
          <label class="mode-radio">
            <input type="radio" name="edit-mode" value="link" bind:group={editMode} />
            <span>Link — navigate directly to URL</span>
          </label>
          <label class="mode-radio">
            <input type="radio" name="edit-mode" value="search" bind:group={editMode} />
            <span>Search — run a search for this query</span>
          </label>
        </div>
      </div>

      <!-- Conditionally show URL input (link mode) or Pin-to-top input (search mode) -->
      {#if editMode === 'link'}
        <label class="modal-input-field">
          <span class="modal-label">URL</span>
          <input
            class="modal-input"
            type="text"
            bind:value={editUrl}
            placeholder="/my-account/port-in-dashboard"
          />
        </label>
      {:else}
        <div class="modal-input-field">
          <span class="modal-label">Pin to top <span class="hint">(optional)</span></span>
          <div class="pin-chips">
            {#each editFeaturedDocIds as docId}
              <span class="pin-chip">
                <span class="pin-chip-text">{docId}</span>
                <button class="pin-chip-remove" on:click={() => removeFeaturedDocId(docId)}>✕</button>
              </span>
            {/each}
            <div class="pin-chip-add-row">
              <input
                class="pin-chip-input"
                type="text"
                bind:value={editFeaturedDocIdInput}
                placeholder="Paste doc ID…"
                on:keydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addFeaturedDocId(); } }}
              />
              {#if editFeaturedDocIdInput.trim()}
                <button class="pin-chip-add-btn" on:click={addFeaturedDocId}>Add</button>
              {/if}
              <button class="pin-browse-btn" on:click={() => showDocPicker = true}>Browse</button>
            </div>
          </div>
        </div>
      {/if}

      <!-- Boost tier toggle -->
      <div class="modal-field">
        <span class="modal-label">Tier</span>
        <div class="boost-toggle">
          <button
            class="boost-btn"
            class:boost-active-suppress={editBoost === -1}
            on:click={() => (editBoost = -1)}
          >Suppress</button>
          <button
            class="boost-btn"
            class:boost-active-normal={editBoost === 0}
            on:click={() => (editBoost = 0)}
          >Normal</button>
          <button
            class="boost-btn"
            class:boost-active-promote={editBoost === 1}
            on:click={() => (editBoost = 1)}
          >Promote</button>
        </div>
      </div>

      {#if editErr}<p class="modal-err">{editErr}</p>{/if}

      <!-- Delete confirmation flow -->
      {#if confirmDelete}
        <div class="confirm-delete">
          <span class="confirm-text">Are you sure? This cannot be undone.</span>
          <div class="confirm-actions">
            <button class="btn-cancel-sm" on:click={() => (confirmDelete = false)} disabled={editSaving}>
              Cancel
            </button>
            <button class="btn-danger" on:click={doDelete} disabled={editSaving}>
              {editSaving ? 'Deleting…' : 'Delete'}
            </button>
          </div>
        </div>
      {/if}

      <div class="modal-actions">
        <!-- Delete button is replaced by confirmation UI when confirmDelete is true. -->
        {#if !confirmDelete}
          <button class="btn-delete" on:click={startDelete} disabled={editSaving}>Delete</button>
        {/if}
        <div class="modal-actions-right">
          <button class="btn-cancel" on:click={closeEdit} disabled={editSaving}>Cancel</button>
          <button class="btn-save" on:click={saveEdit} disabled={editSaving}>
            {editSaving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  </div>
{/if}

<!-- ── Add modal ───────────────────────────────────────────────────────────── -->
{#if addModal}
  <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
  <div class="overlay" on:click|self={closeAdd}>
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-header">
        <h3>Add suggestion</h3>
        <button class="btn-close" on:click={closeAdd}>✕</button>
      </div>

      <label class="modal-input-field">
        <span class="modal-label">Query <span class="req">*</span></span>
        <!-- svelte-ignore a11y-autofocus -->
        <input
          class="modal-input"
          type="text"
          bind:value={addQuery}
          placeholder="e.g. wireless headphones"
          autofocus
        />
      </label>

      <label class="modal-input-field">
        <span class="modal-label">Category</span>
        <input
          class="modal-input"
          type="text"
          bind:value={addCategory}
          placeholder="e.g. electronics"
        />
      </label>

      <!-- Boost tier toggle (defaults to Promote so manual additions are visible immediately) -->
      <div class="modal-field">
        <span class="modal-label">Tier</span>
        <div class="boost-toggle">
          <button
            class="boost-btn"
            class:boost-active-suppress={addBoost === -1}
            on:click={() => (addBoost = -1)}
          >Suppress</button>
          <button
            class="boost-btn"
            class:boost-active-normal={addBoost === 0}
            on:click={() => (addBoost = 0)}
          >Normal</button>
          <button
            class="boost-btn"
            class:boost-active-promote={addBoost === 1}
            on:click={() => (addBoost = 1)}
          >Promote</button>
        </div>
      </div>

      {#if addErr}<p class="modal-err">{addErr}</p>{/if}

      <div class="modal-actions">
        <div></div>
        <div class="modal-actions-right">
          <button class="btn-cancel" on:click={closeAdd} disabled={addSaving}>Cancel</button>
          <button class="btn-save" on:click={submitAdd} disabled={addSaving || !addQuery.trim()}>
            {addSaving ? 'Adding…' : 'Add'}
          </button>
        </div>
      </div>
    </div>
  </div>
{/if}

{#if showDocPicker}
  <DocPickerModal
    {indexId}
    selectedIds={editFeaturedDocIds}
    on:select={(e) => selectPickerDoc(e.detail)}
    on:close={() => showDocPicker = false}
  />
{/if}

<style>
  /* ── Panel layout ─────────────────────────────────────────────────────────── */
  .suggestions-panel {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  /* ── Toolbar ──────────────────────────────────────────────────────────────── */
  .toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .search-input {
    flex: 1;
    max-width: 320px;
    background: var(--bg-3);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 7px 10px;
    color: var(--text-2);
    font-size: 13px;
    font-family: inherit;
  }
  .search-input:focus { outline: none; border-color: var(--accent); }
  .count { font-size: 13px; color: var(--text-4); flex: 1; }
  .btn-refresh {
    background: none;
    border: none;
    color: var(--text-5);
    cursor: pointer;
    font-size: 16px;
  }
  .btn-refresh:hover { color: var(--accent); }
  .btn-add {
    background: var(--accent);
    border: none;
    border-radius: 6px;
    color: #fff;
    font-size: 12px;
    font-weight: 600;
    padding: 7px 14px;
    cursor: pointer;
    flex-shrink: 0;
  }
  .btn-add:hover { background: var(--accent-hover); }

  /* ── Error + empty states ─────────────────────────────────────────────────── */
  .error-banner {
    background: #1a0f0f;
    border: 1px solid var(--danger-border);
    border-radius: 8px;
    color: var(--danger-text);
    font-size: 13px;
    padding: 12px 14px;
  }
  .empty {
    color: var(--text-5);
    font-size: 13px;
    padding: 32px 0;
    text-align: center;
  }
  .no-label-notice {
    background: var(--bg-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-4);
    line-height: 1.6;
    padding: 24px;
    text-align: center;
  }
  .no-label-notice strong { color: var(--text-2); }

  /* ── Tier sections ────────────────────────────────────────────────────────── */
  .tier-section {
    display: flex;
    flex-direction: column;
    gap: 0;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  .tier-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    border-bottom: 1px solid var(--border);
  }
  .tier-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .tier-count {
    font-size: 11px;
    color: var(--text-5);
    background: var(--surface);
    border-radius: 10px;
    padding: 1px 7px;
  }

  /* Tier-specific header colours */
  .tier-promoted   { background: #0d1f12; }
  .tier-promoted .tier-label   { color: #5cdb85; }
  .tier-organic    { background: var(--bg-3); }
  .tier-organic .tier-label    { color: var(--text-4); }
  .tier-suppressed { background: #1a0f0f; }
  .tier-suppressed .tier-label { color: var(--danger-text); }

  .tier-empty {
    color: var(--text-5);
    font-size: 12px;
    padding: 14px;
    text-align: center;
  }

  /* ── Suggestion rows ──────────────────────────────────────────────────────── */
  .suggestion-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
  }
  .suggestion-row:last-child { border-bottom: none; }
  .suggestion-row-suppressed { opacity: 0.55; }

  .query-text {
    flex: 1;
    color: var(--text-2);
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .pop {
    font-size: 12px;
    color: var(--text-5);
    flex-shrink: 0;
  }

  /* ── Badges ───────────────────────────────────────────────────────────────── */
  .badge {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    padding: 2px 7px;
    border-radius: 4px;
    flex-shrink: 0;
  }
  .badge-cat  { background: var(--accent-dim); color: var(--accent); }
  .badge-type { background: var(--surface);    color: var(--text-4); }
  /* Green tint badge for suggestions that navigate directly to a URL. */
  .badge-link { background: #1a2a1a; color: #5cdb85; }

  /* ── Row edit button ──────────────────────────────────────────────────────── */
  .btn-edit {
    background: none;
    border: 1px solid var(--border-2);
    border-radius: 5px;
    color: var(--text-4);
    font-size: 11px;
    padding: 3px 10px;
    cursor: pointer;
    flex-shrink: 0;
  }
  .btn-edit:hover { border-color: var(--accent); color: var(--accent); }

  /* ── Pagination ───────────────────────────────────────────────────────────── */
  .pagination {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 13px;
    color: var(--text-4);
  }
  .pagination button {
    background: var(--surface);
    border: 1px solid var(--border-2);
    color: var(--text-4);
    border-radius: 5px;
    padding: 5px 12px;
    cursor: pointer;
    font-size: 12px;
  }
  .pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
  .pagination button:not(:disabled):hover { border-color: var(--accent); color: var(--accent); }

  /* ── Modal overlay ────────────────────────────────────────────────────────── */
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
  }
  .modal {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 22px 24px;
    width: 100%;
    max-width: 460px;
    display: flex;
    flex-direction: column;
    gap: 14px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  }
  .modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .modal-header h3 { margin: 0; font-size: 14px; font-weight: 600; color: var(--text-2); }
  .btn-close {
    background: none;
    border: none;
    color: var(--text-5);
    font-size: 14px;
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 4px;
  }
  .btn-close:hover { color: var(--text-2); }

  /* ── Modal fields ─────────────────────────────────────────────────────────── */
  .modal-field {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .modal-label {
    font-size: 12px;
    color: var(--text-4);
    min-width: 80px;
    flex-shrink: 0;
  }
  .modal-val {
    font-size: 13px;
    color: var(--text-2);
  }
  .modal-input-field {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .modal-input {
    background: var(--bg-3);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 7px 10px;
    color: var(--text-2);
    font-size: 13px;
    font-family: inherit;
    width: 100%;
    box-sizing: border-box;
  }
  .modal-input:focus { outline: none; border-color: var(--accent); }
  .req { color: var(--danger-text); font-size: 10px; }
  /* Column-direction modal-field variant used for the mode radio group. */
  .modal-field-col { align-items: flex-start; flex-direction: column; gap: 8px; }
  /* Radio group container for the mode selector. */
  .mode-radios { display: flex; flex-direction: column; gap: 6px; }
  /* Individual radio option row. */
  .mode-radio { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-3); cursor: pointer; }
  .mode-radio input { accent-color: var(--accent); }
  /* Dimmed label for optional field markers. */
  .hint { font-size: 10px; color: var(--text-5); }
  /* Pin-to-top multi-value chip input */
  .pin-chips { display: flex; flex-direction: column; gap: 6px; }
  .pin-chip {
    display: inline-flex; align-items: center; gap: 6px;
    background: var(--bg-3); border: 1px solid var(--border);
    border-radius: 4px; padding: 3px 8px; font-size: 12px;
    color: var(--text-3); max-width: 100%; overflow: hidden;
  }
  .pin-chip-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .pin-chip-remove {
    background: none; border: none; cursor: pointer; color: var(--text-5);
    font-size: 10px; padding: 0; line-height: 1; flex-shrink: 0;
  }
  .pin-chip-remove:hover { color: var(--danger-text); }
  .pin-chip-add-row { display: flex; gap: 6px; align-items: center; }
  .pin-chip-input {
    flex: 1; background: var(--bg-2); border: 1px solid var(--border);
    border-radius: 4px; padding: 5px 8px; font-size: 12px; color: var(--text);
    font-family: inherit;
  }
  .pin-chip-input:focus { outline: none; border-color: var(--accent); }
  .pin-chip-add-btn {
    background: var(--accent); border: none; border-radius: 4px; color: #fff;
    font-size: 12px; padding: 5px 10px; cursor: pointer; flex-shrink: 0;
  }
  .pin-chip-add-btn:hover { opacity: 0.85; }
  .pin-browse-btn {
    background: var(--bg-3); border: 1px solid var(--border); border-radius: 4px;
    color: var(--text-3); font-size: 12px; padding: 5px 10px; cursor: pointer; flex-shrink: 0;
  }
  .pin-browse-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* ── Boost tier toggle ────────────────────────────────────────────────────── */
  .boost-toggle {
    display: flex;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .boost-btn {
    background: var(--bg-3);
    border: none;
    border-right: 1px solid var(--border);
    color: var(--text-4);
    font-size: 12px;
    padding: 6px 14px;
    cursor: pointer;
    flex: 1;
    transition: background 0.1s, color 0.1s;
  }
  .boost-btn:last-child { border-right: none; }
  .boost-btn:hover { color: var(--text-2); }

  /* Active state colour per tier. */
  .boost-active-suppress { background: #1a0f0f; color: var(--danger-text); font-weight: 600; }
  .boost-active-normal   { background: var(--accent-dim); color: var(--accent); font-weight: 600; }
  .boost-active-promote  { background: #0d1f12; color: #5cdb85; font-weight: 600; }

  /* ── Modal actions bar ────────────────────────────────────────────────────── */
  .modal-actions {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    padding-top: 4px;
  }
  .modal-actions-right { display: flex; gap: 8px; }

  .modal-err {
    font-size: 12px;
    color: var(--danger-text);
    margin: 0;
  }

  /* ── Shared button styles ─────────────────────────────────────────────────── */
  .btn-save {
    background: var(--accent);
    border: none;
    border-radius: 6px;
    color: #fff;
    font-size: 12px;
    font-weight: 600;
    padding: 7px 18px;
    cursor: pointer;
  }
  .btn-save:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-save:disabled { opacity: 0.4; cursor: not-allowed; }

  .btn-cancel {
    background: none;
    border: 1px solid var(--border-2);
    border-radius: 6px;
    color: var(--text-4);
    font-size: 12px;
    padding: 7px 16px;
    cursor: pointer;
  }
  .btn-cancel:hover:not(:disabled) { border-color: var(--text-5); color: var(--text-2); }
  .btn-cancel:disabled { opacity: 0.4; cursor: not-allowed; }

  .btn-delete {
    background: none;
    border: 1px solid var(--danger-border);
    border-radius: 6px;
    color: var(--danger-text);
    font-size: 12px;
    padding: 7px 16px;
    cursor: pointer;
  }
  .btn-delete:hover:not(:disabled) { background: #1a0f0f; }
  .btn-delete:disabled { opacity: 0.4; cursor: not-allowed; }

  /* ── Delete confirmation inline block ────────────────────────────────────── */
  .confirm-delete {
    background: #1a0f0f;
    border: 1px solid var(--danger-border);
    border-radius: 8px;
    padding: 10px 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .confirm-text { font-size: 12px; color: var(--danger-text); }
  .confirm-actions { display: flex; gap: 8px; justify-content: flex-end; }

  .btn-cancel-sm {
    background: none;
    border: 1px solid var(--border-2);
    border-radius: 5px;
    color: var(--text-4);
    font-size: 11px;
    padding: 5px 12px;
    cursor: pointer;
  }
  .btn-cancel-sm:hover:not(:disabled) { color: var(--text-2); }

  .btn-danger {
    background: #3a1e1e;
    border: 1px solid var(--danger-border);
    border-radius: 5px;
    color: var(--danger-text);
    font-size: 11px;
    font-weight: 600;
    padding: 5px 14px;
    cursor: pointer;
  }
  .btn-danger:hover:not(:disabled) { background: #4a2020; }
  .btn-danger:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
