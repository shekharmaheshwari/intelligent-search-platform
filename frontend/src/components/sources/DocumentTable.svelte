<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  /**
   * DocumentTable — paginated table of indexed documents.
   *
   * Used in the IndexDetail "Documents" tab.  Renders a four-column table
   * (Title, Source, URL, Indexed) with prev/next pagination and a manual refresh button.
   */
  import { onMount, onDestroy } from 'svelte';
  import { documentsStore } from '../../lib/stores/documents-store';
  import { fmtDate } from './DocumentTable.helpers';

  export let indexId: string;

  const perPage = 20;

  $: docs    = $documentsStore.docs;
  $: found   = $documentsStore.found;
  $: page    = $documentsStore.page;
  $: loading = $documentsStore.loading;

  function load() { documentsStore.load(indexId, { page, perPage }); }

  onMount(load);
  onDestroy(() => documentsStore.reset());

  function prev() { if (page > 1) documentsStore.load(indexId, { page: page - 1, perPage }); }
  function next() { if (page * perPage < found) documentsStore.load(indexId, { page: page + 1, perPage }); }
</script>

<div class="doc-table">
  <div class="toolbar">
    <span class="count">{found.toLocaleString()} documents</span>
    <button on:click={load} class="refresh" title="Refresh">↻</button>
  </div>

  {#if loading}
    <div class="empty">Loading…</div>
  {:else if docs.length === 0}
    <div class="empty">No documents yet. Run an ingestion to populate this index.</div>
  {:else}
    <table>
      <thead>
        <tr>
          <th>Title</th>
          <th>Source</th>
          <th>URL</th>
          <th>Indexed</th>
        </tr>
      </thead>
      <tbody>
        {#each docs as doc}
          <tr>
            <td class="title-cell">{doc.title || '—'}</td>
            <td><span class="badge">{doc.source_type}</span></td>
            <td class="url-cell">
              {#if doc.url}
                <a href={doc.url} target="_blank" rel="noopener">{doc.url}</a>
              {:else}—{/if}
            </td>
            <td>{fmtDate(doc.indexed_at)}</td>
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

<style>
  .doc-table { display: flex; flex-direction: column; gap: 12px; }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .count { font-size: 13px; color: var(--text-4); }
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
