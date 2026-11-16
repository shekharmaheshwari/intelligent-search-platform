<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  /**
   * DocumentTable — paginated table of indexed documents.
   *
   * Used in the IndexDetail "Documents" tab.  Renders a table (Title, Source,
   * URL, Indexed) with prev/next pagination, a refresh button, an origin filter
   * (All / Manual / Connector) and — for ISP-managed indexes — an "Add document"
   * button plus Edit on manually entered rows.
   */
  import { onMount, onDestroy } from 'svelte';
  import { documentsStore } from '../../lib/stores/documents-store';
  import type { DocumentOrigin } from '../../lib/types';
  import { fmtDate } from './DocumentTable.helpers';
  import ManualDocumentModal from './ManualDocumentModal.svelte';

  export let indexId: string;
  /** External (shared catalog) indexes do not support manual entry. */
  export let isExternal = false;

  const perPage = 20;
  const origins: { value: DocumentOrigin; label: string }[] = [
    { value: 'all',       label: 'All' },
    { value: 'manual',    label: 'Manual' },
    { value: 'connector', label: 'Connector' },
  ];

  $: docs    = $documentsStore.docs;
  $: found   = $documentsStore.found;
  $: page    = $documentsStore.page;
  $: origin  = $documentsStore.origin;
  $: loading = $documentsStore.loading;

  /** ``undefined`` = modal closed, ``null`` = adding, string = editing that doc. */
  let modalDocId: string | null | undefined = undefined;

  function load() { documentsStore.load(indexId, { page, perPage, origin }); }
  function setOrigin(o: DocumentOrigin) { documentsStore.load(indexId, { page: 1, perPage, origin: o }); }

  onMount(load);
  onDestroy(() => documentsStore.reset());

  function prev() { if (page > 1) documentsStore.load(indexId, { page: page - 1, perPage, origin }); }
  function next() { if (page * perPage < found) documentsStore.load(indexId, { page: page + 1, perPage, origin }); }

  function onSaved() {
    modalDocId = undefined;
    load();
  }
</script>

<div class="doc-table">
  <div class="toolbar">
    {#if !isExternal}
      <div class="seg" role="group" aria-label="Filter by origin">
        {#each origins as o}
          <button class:active={origin === o.value} on:click={() => setOrigin(o.value)}>{o.label}</button>
        {/each}
      </div>
    {/if}
    <span class="count">{found.toLocaleString()} documents</span>
    <span class="spacer"></span>
    <button on:click={load} class="refresh" title="Refresh">↻</button>
    {#if !isExternal}
      <button class="btn-add" on:click={() => (modalDocId = null)}>+ Add document</button>
    {/if}
  </div>

  {#if loading}
    <div class="empty">Loading…</div>
  {:else if docs.length === 0}
    <div class="empty">
      {#if origin === 'manual'}No manual documents yet. Use “+ Add document” to create one.
      {:else}No documents yet. Run an ingestion to populate this index.{/if}
    </div>
  {:else}
    <table>
      <thead>
        <tr>
          <th>Title</th>
          <th>Source</th>
          <th>URL</th>
          <th>Indexed</th>
          {#if !isExternal}<th></th>{/if}
        </tr>
      </thead>
      <tbody>
        {#each docs as doc}
          <tr>
            <td class="title-cell">{doc.title || '—'}</td>
            <td><span class="badge" class:manual={doc.source_type === 'manual'}>{doc.source_type}</span></td>
            <td class="url-cell">
              {#if doc.url}
                <a href={doc.url} target="_blank" rel="noopener">{doc.url}</a>
              {:else}—{/if}
            </td>
            <td>{fmtDate(doc.indexed_at)}</td>
            {#if !isExternal}
              <td class="action-cell">
                {#if doc.source_type === 'manual'}
                  <button class="btn-edit" on:click={() => (modalDocId = doc.id)}>Edit</button>
                {/if}
              </td>
            {/if}
          </tr>
        {/each}
      </tbody>
    </table>
    <div class="pagination">
      <button on:click={prev} disabled={page === 1}>← Prev</button>
      <span>Page {page}</span>
      <button on:click={next} disabled={page * perPage >= found}>Next →</button>
    </div>
  {/if}
</div>

{#if modalDocId !== undefined}
  <ManualDocumentModal
    {indexId}
    docId={modalDocId}
    on:saved={onSaved}
    on:close={() => (modalDocId = undefined)}
  />
{/if}

<style>
  .doc-table { display: flex; flex-direction: column; gap: 12px; }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .count { font-size: 13px; color: var(--text-4); }
  .spacer { flex: 1; }
  .seg {
    display: inline-flex;
    border: 1px solid var(--border-2);
    border-radius: 6px;
    overflow: hidden;
  }
  .seg button {
    background: none;
    border: none;
    border-right: 1px solid var(--border-2);
    color: var(--text-4);
    font-size: 12px;
    padding: 5px 12px;
    cursor: pointer;
  }
  .seg button:last-child { border-right: none; }
  .seg button:hover { color: var(--text-2); }
  .seg button.active { background: var(--accent-dim); color: var(--accent); font-weight: 600; }
  .btn-add {
    background: var(--accent);
    border: none;
    border-radius: 6px;
    color: #fff;
    font-size: 12px;
    font-weight: 600;
    padding: 7px 14px;
    cursor: pointer;
  }
  .btn-add:hover { background: var(--accent-hover); }
  .action-cell { text-align: right; width: 1%; white-space: nowrap; }
  .btn-edit {
    background: none;
    border: 1px solid var(--border-2);
    border-radius: 5px;
    color: var(--text-4);
    font-size: 12px;
    padding: 3px 10px;
    cursor: pointer;
  }
  .btn-edit:hover { border-color: var(--accent); color: var(--accent); }
  .refresh {
    background: none; border: none; color: var(--text-5); cursor: pointer; font-size: 16px;
  }
  .refresh:hover { color: var(--accent); }
  .empty { color: var(--text-5); font-size: 13px; padding: 32px 0; text-align: center; }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th {
    text-align: left;
    color: var(--text-5);
    font-weight: 500;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }
  td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    color: var(--text-2);
    vertical-align: top;
  }
  .title-cell {
    max-width: 240px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text-2);
  }
  .url-cell {
    max-width: 200px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .url-cell a { color: var(--accent); text-decoration: none; }
  .url-cell a:hover { text-decoration: underline; }
  .badge {
    background: var(--accent-dim);
    color: var(--accent);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    padding: 2px 6px;
    border-radius: 3px;
  }
  .badge.manual { background: none; box-shadow: inset 0 0 0 1px var(--accent-border); }
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
</style>
