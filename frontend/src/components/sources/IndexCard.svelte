<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { IndexMeta } from '../../lib/types';
  import { fmtDate } from './IndexCard.helpers';

  export let index: IndexMeta;

  const dispatch = createEventDispatcher<{ open: string; delete: string }>();
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
    background: var(--bg-hover);
    border: 1px solid var(--border-2);
    border-radius: 10px;
    padding: 20px;
    cursor: pointer;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .card:hover {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent);
  }
  .top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 12px;
  }
  .badge {
    background: var(--accent-dim);
    color: var(--accent);
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
    color: var(--text-6);
    cursor: pointer;
    font-size: 14px;
    line-height: 1;
    padding: 2px 4px;
  }
  .del:hover { color: var(--danger-text); }
  h3 {
    margin: 0 0 6px;
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
  }
  .desc {
    margin: 0 0 14px;
    font-size: 12px;
    color: var(--text-4);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .meta {
    display: flex;
    gap: 6px;
    font-size: 12px;
    color: var(--text-5);
  }
</style>
