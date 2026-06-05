<script lang="ts">
  /**
   * IndexDetail — tabbed detail view for a single ISP index.
   *
   * Shown when the user navigates to the configure view from SourceContent.
   * Tabs: Overview (connectors + last ingest status), Documents, Search, API.
   * Dispatches ``back`` when the breadcrumb "← Indexes" button is clicked.
   */
  import { onMount } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { api } from '../lib/api';
  import type { IndexMeta, IngestStatus } from '../lib/types';
  import DocumentTable from './DocumentTable.svelte';
  import SearchPanel from './SearchPanel.svelte';
  import ApiPanel from './ApiPanel.svelte';

  export let indexId: string;
  export let tab: 'overview' | 'documents' | 'search' | 'api' = 'overview';

  const tabs: Array<'overview' | 'documents' | 'search' | 'api'> = ['overview', 'documents', 'search', 'api'];

  const dispatch = createEventDispatcher<{ back: void }>();

  let meta: IndexMeta | null = null;
  let status: IngestStatus | null = null;

  onMount(async () => {
    meta = await api.getIndex(indexId);
    status = await api.getStatus(indexId);
  });

  /** Format an ISO date string to a short human-readable locale date (e.g. "Jun 5, 2026"). */
  function fmtDate(iso: string) {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }
</script>

<div class="detail">
  <div class="breadcrumb">
    <button on:click={() => dispatch('back')}>← Indexes</button>
    {#if meta}<span>/ {meta.name}</span>{/if}
  </div>

  {#if !meta}
    <div class="loading">Loading…</div>
  {:else}
    <div class="detail-head">
      <div>
        <h1>{meta.name}</h1>
        {#if meta.description}<p class="desc">{meta.description}</p>{/if}
      </div>
      <div class="stats">
        <div class="stat">
          <span class="stat-val">{meta.doc_count.toLocaleString()}</span>
          <span class="stat-lbl">Documents</span>
        </div>
        <div class="stat">
          <span class="stat-val">{meta.connectors.length}</span>
          <span class="stat-lbl">Connectors</span>
        </div>
        <div class="stat">
          <span class="stat-val">{fmtDate(meta.created_at)}</span>
          <span class="stat-lbl">Created</span>
        </div>
      </div>
    </div>

    <div class="tabs">
      {#each tabs as t}
        <button class:active={tab === t} on:click={() => tab = t}>
          {t.charAt(0).toUpperCase() + t.slice(1)}
        </button>
      {/each}
    </div>

    <div class="tab-body">
      {#if tab === 'overview'}
        <div class="overview">
          {#if status && status.state !== 'idle'}
            <div class="status-card" class:running={status.state === 'running'}
              class:done={status.state === 'done'} class:error={status.state === 'error'}>
              <div class="status-row">
                <span class="status-label">Last ingest</span>
                <span class="status-badge">{status.state}</span>
              </div>
              {#if status.state === 'done' || status.state === 'running'}
                <p>{status.indexed} / {status.total || '?'} documents indexed</p>
              {/if}
              {#if status.error}
                <p class="err">{status.error}</p>
              {/if}
            </div>
          {/if}

          {#if meta.connectors.length > 0}
            <h2>Connectors</h2>
            {#each meta.connectors as conn}
              <div class="connector-card">
                <div class="conn-type">{conn.type}</div>
                <div class="conn-url">{String(conn.url ?? '')}</div>
              </div>
            {/each}
          {:else}
            <div class="empty-connectors">
              No connectors configured. Use the ingest endpoints to add data to this index.
            </div>
          {/if}
        </div>

      {:else if tab === 'documents'}
        <DocumentTable {indexId} />

      {:else if tab === 'search'}
        <SearchPanel {indexId} />

      {:else if tab === 'api'}
        <ApiPanel {indexId} />
      {/if}
    </div>
  {/if}
</div>

<style>
  .detail { display: flex; flex-direction: column; gap: 0; }
  .breadcrumb {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 20px;
    font-size: 13px;
    color: #666;
  }
  .breadcrumb button {
    background: none; border: none; color: #7c6cf4; cursor: pointer; font-size: 13px;
  }
  .breadcrumb button:hover { text-decoration: underline; }
  .loading { color: #555; padding: 40px 0; text-align: center; }
  .detail-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 24px;
    flex-wrap: wrap;
    gap: 16px;
  }
  h1 { margin: 0 0 4px; font-size: 22px; color: #eee; }
  .desc { margin: 0; font-size: 13px; color: #666; }
  .stats { display: flex; gap: 24px; }
  .stat { display: flex; flex-direction: column; align-items: flex-end; }
  .stat-val { font-size: 18px; font-weight: 600; color: #eee; }
  .stat-lbl { font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 0.5px; }
  .tabs {
    display: flex;
    border-bottom: 1px solid #1e1e1e;
    margin-bottom: 24px;
  }
  .tabs button {
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 10px 16px;
    color: #666;
    font-size: 13px;
    cursor: pointer;
    margin-bottom: -1px;
  }
  .tabs button.active {
    color: #7c6cf4;
    border-bottom-color: #7c6cf4;
  }
  .tabs button:hover:not(.active) { color: #aaa; }
  .tab-body { min-height: 200px; }
  .overview { display: flex; flex-direction: column; gap: 16px; }
  h2 { font-size: 14px; color: #888; font-weight: 500; margin: 0; }
  .status-card {
    background: #0f1a15;
    border: 1px solid #1e3028;
    border-radius: 8px;
    padding: 14px 16px;
  }
  .status-card.running { background: #0f1520; border-color: #1e2a40; }
  .status-card.error { background: #1a0f0f; border-color: #3a1e1e; }
  .status-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
  .status-label { font-size: 12px; color: #666; }
  .status-badge {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    padding: 2px 7px;
    border-radius: 4px;
    background: #1e3028;
    color: #5cdb85;
  }
  .status-card.running .status-badge { background: #1e2a40; color: #70a8ff; }
  .status-card.error .status-badge { background: #3a1e1e; color: #e55; }
  .status-card p { margin: 0; font-size: 12px; color: #777; }
  .err { color: #e55 !important; }
  .connector-card {
    background: #111;
    border: 1px solid #1e1e1e;
    border-radius: 8px;
    padding: 12px 14px;
    display: flex;
    gap: 12px;
    align-items: center;
  }
  .conn-type {
    background: #1e1b3a;
    color: #9d91f5;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    padding: 2px 7px;
    border-radius: 4px;
    flex-shrink: 0;
  }
  .conn-url { font-size: 12px; color: #666; word-break: break-all; }
  .empty-connectors { font-size: 13px; color: #555; text-align: center; padding: 32px 0; }
</style>
