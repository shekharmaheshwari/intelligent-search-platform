<script lang="ts">
  /**
   * Dashboard — grid overview of all ISP indexes.
   *
   * Renders a card grid via IndexCard when indexes exist, or an empty-state CTA otherwise.
   * Dispatches ``open`` (index id), ``create``, and ``delete`` (index id) for App.svelte to handle.
   */
  import { createEventDispatcher } from 'svelte';
  import type { IndexMeta } from '../../lib/types';
  import IndexCard from './IndexCard.svelte';

  export let indexes: IndexMeta[] = [];

  const dispatch = createEventDispatcher<{ open: string; create: void; delete: string }>();
</script>

<div class="dashboard">
  <div class="dash-head">
    <div>
      <h1>Indexes</h1>
      <p class="sub">{indexes.length} index{indexes.length !== 1 ? 'es' : ''}</p>
    </div>
    <button class="btn-create" on:click={() => dispatch('create')}>+ New Index</button>
  </div>

  {#if indexes.length === 0}
    <div class="empty">
      <div class="empty-icon">⬡</div>
      <h2>No indexes yet</h2>
      <p>Create an index to start ingesting and searching your content.</p>
      <button class="btn-create" on:click={() => dispatch('create')}>Create your first index</button>
    </div>
  {:else}
    <div class="grid">
      {#each indexes as idx}
        <IndexCard index={idx}
          on:open={e => dispatch('open', e.detail)}
          on:delete={e => dispatch('delete', e.detail)}
        />
      {/each}
    </div>
  {/if}
</div>

<style>
  .dashboard { display: flex; flex-direction: column; gap: 0; }
  .dash-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 28px;
  }
  h1 { margin: 0 0 4px; font-size: 24px; color: #eee; }
  .sub { margin: 0; font-size: 13px; color: #555; }
  .btn-create {
    background: #7c6cf4;
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 9px 18px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .btn-create:hover { background: #6b5ce7; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 16px;
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    padding: 80px 0;
    text-align: center;
  }
  .empty-icon { font-size: 40px; color: #2a2a2a; }
  .empty h2 { margin: 0; font-size: 18px; color: #555; }
  .empty p { margin: 0; font-size: 13px; color: #444; max-width: 320px; }
</style>
