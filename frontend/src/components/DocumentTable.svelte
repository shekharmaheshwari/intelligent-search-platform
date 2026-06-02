<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../lib/api';
  import type { DocumentItem } from '../lib/types';

  export let indexId: string;

  let docs: DocumentItem[] = [];
  let found = 0;
  let page = 1;
  const perPage = 20;
  let loading = false;

  async function load() {
    loading = true;
    try {
      const res = await api.listDocuments(indexId, page, perPage);
      docs = res.hits.map(h => h.document);
      found = res.found;
    } finally {
      loading = false;
    }
  }

  onMount(load);

  function fmtDate(ts: number) {
    return new Date(ts * 1000).toLocaleDateString();
  }

  function prev() { if (page > 1) { page--; load(); } }
  function next() { if (page * perPage < found) { page++; load(); } }
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
  .count { font-size: 13px; color: #666; }
  .refresh {
    background: none; border: none; color: #555; cursor: pointer; font-size: 16px;
  }
  .refresh:hover { color: #9d91f5; }
  .empty { color: #555; font-size: 13px; padding: 32px 0; text-align: center; }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th {
    text-align: left;
    color: #555;
    font-weight: 500;
    padding: 8px 12px;
    border-bottom: 1px solid #1e1e1e;
  }
  td {
    padding: 10px 12px;
    border-bottom: 1px solid #1a1a1a;
    color: #ccc;
    vertical-align: top;
  }
  .title-cell {
    max-width: 240px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: #ddd;
  }
  .url-cell {
    max-width: 200px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .url-cell a { color: #7c6cf4; text-decoration: none; }
  .url-cell a:hover { text-decoration: underline; }
  .badge {
    background: #1e1b3a;
    color: #9d91f5;
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
    color: #666;
  }
  .pagination button {
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    color: #888;
    border-radius: 5px;
    padding: 5px 12px;
    cursor: pointer;
    font-size: 12px;
  }
  .pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
  .pagination button:not(:disabled):hover { border-color: #7c6cf4; color: #9d91f5; }
</style>
