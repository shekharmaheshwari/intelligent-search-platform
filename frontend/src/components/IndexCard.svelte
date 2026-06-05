<script lang="ts">
  /**
   * IndexCard — compact card for a single ISP index shown in the Dashboard grid.
   *
   * Clicking anywhere on the card dispatches ``open``; the ✕ button dispatches ``delete``
   * with stopPropagation so it does not also trigger ``open``.
   */
  import { createEventDispatcher } from 'svelte';
  import type { IndexMeta } from '../lib/types';

  export let index: IndexMeta;

  const dispatch = createEventDispatcher<{ open: string; delete: string }>();

  /** Format an ISO date string to a short locale date (e.g. "Jun 5, 2026"). */
  function fmtDate(iso: string) {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }
</script>

<div class="card" on:click={() => dispatch('open', index.id)} role="button" tabindex="0"
  on:keydown={e => e.key === 'Enter' && dispatch('open', index.id)}>
  <div class="top">
    <div class="badge">{index.connectors[0]?.type ?? 'empty'}</div>
    <button class="del" on:click|stopPropagation={() => dispatch('delete', index.id)}
      aria-label="Delete index">✕</button>
  </div>
  <h3>{index.name}</h3>
  {#if index.description}
    <p class="desc">{index.description}</p>
  {/if}
  <div class="meta">
    <span>{index.doc_count.toLocaleString()} docs</span>
    <span>·</span>
    <span>{fmtDate(index.created_at)}</span>
  </div>
</div>

<style>
  .card {
    background: #141414;
    border: 1px solid #222;
    border-radius: 10px;
    padding: 20px;
    cursor: pointer;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .card:hover {
    border-color: #7c6cf4;
    box-shadow: 0 0 0 1px #7c6cf4;
  }
  .top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 12px;
  }
  .badge {
    background: #1e1b3a;
    color: #9d91f5;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 3px 8px;
    border-radius: 4px;
  }
  .del {
    background: none;
    border: none;
    color: #444;
    cursor: pointer;
    font-size: 14px;
    line-height: 1;
    padding: 2px 4px;
  }
  .del:hover { color: #e55; }
  h3 {
    margin: 0 0 6px;
    font-size: 15px;
    font-weight: 600;
    color: #eee;
  }
  .desc {
    margin: 0 0 14px;
    font-size: 12px;
    color: #666;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .meta {
    display: flex;
    gap: 6px;
    font-size: 12px;
    color: #555;
  }
</style>
