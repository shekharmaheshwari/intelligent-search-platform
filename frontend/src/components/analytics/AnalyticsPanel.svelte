<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import type { IndexMeta } from '../../lib/types';
  import { analyticsStore } from '../../lib/store';
  import { analyticsController } from '../../lib/controller/analyticsController';
  import SummaryCards from './SummaryCards.svelte';
  import SearchVolume from './SearchVolume.svelte';
  import TopSearches  from './TopSearches.svelte';
  import ZeroResults  from './ZeroResults.svelte';

  export let indexes: IndexMeta[] = [];

  $: st = $analyticsStore;

  let selectedIndexId = '';

  onMount(() => {
    if (indexes.length > 0) {
      selectedIndexId = indexes[0].id;
      analyticsController.init(selectedIndexId);
    }
  });

  onDestroy(() => analyticsController.teardown());

  function onIndexChange() {
    analyticsController.changeIndex(selectedIndexId);
  }

  function onRangeChange(range: typeof st.range) {
    analyticsController.changeRange(range);
  }

  const RANGES: { label: string; value: '7d' | '30d' | '90d' }[] = [
    { label: '7 days',  value: '7d'  },
    { label: '30 days', value: '30d' },
    { label: '90 days', value: '90d' },
  ];
</script>

<div class="panel">
  <div class="panel-header">
    <div class="header-left">
      <h2 class="panel-title">Analytics</h2>
      <select class="index-select" bind:value={selectedIndexId} on:change={onIndexChange}>
        {#each indexes as index}
          <option value={index.id}>{index.name}</option>
        {/each}
      </select>
    </div>
    <div class="range-tabs">
      {#each RANGES as r}
        <button
          class="range-btn"
          class:active={st.range === r.value}
          on:click={() => onRangeChange(r.value)}
        >{r.label}</button>
      {/each}
    </div>
  </div>

  {#if st.loading}
    <div class="loading">
      <div class="spinner"></div>
      <span>Loading analytics…</span>
    </div>

  {:else if st.error}
    <div class="error-state">
      <div class="error-icon">⚠</div>
      <p class="error-title">Analytics unavailable</p>
      <p class="error-body">{st.error}</p>
      <p class="error-hint">The analytics endpoint may not be set up yet on this ISP instance.</p>
    </div>

  {:else if st.data}
    <SummaryCards summary={st.data.summary} />
    <SearchVolume volume={st.data.volume} />
    <div class="tables-row">
      <TopSearches queries={st.data.top_queries} />
      <ZeroResults queries={st.data.zero_results} />
    </div>

  {:else}
    <div class="empty-state">
      <div class="empty-icon">📊</div>
      <p>Select an index to view analytics.</p>
    </div>
  {/if}
</div>

<style>
  .panel        { padding:28px 32px; max-width:1100px; display:flex; flex-direction:column; gap:20px; }

  .panel-header { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; }
  .header-left  { display:flex; align-items:center; gap:14px; }
  .panel-title  { margin:0; font-size:18px; font-weight:600; color:var(--text); }

  .index-select { padding:6px 10px; border:1px solid var(--border-3); border-radius:6px;
                  background:var(--bg-2); color:var(--text-3); font-size:12px; outline:none; }
  .index-select:focus { border-color:var(--accent); }

  .range-tabs   { display:flex; gap:4px; }
  .range-btn    { padding:5px 12px; border:1px solid var(--border-3); border-radius:6px;
                  background:var(--bg-2); color:var(--text-5); font-size:12px; cursor:pointer; }
  .range-btn.active { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
  .range-btn:hover:not(.active) { border-color:var(--accent); color:var(--accent); }

  .tables-row   { display:grid; grid-template-columns:1fr 1fr; gap:14px; }

  .loading      { display:flex; align-items:center; gap:12px; padding:60px 0; justify-content:center;
                  font-size:13px; color:var(--text-6); }
  .spinner      { width:20px; height:20px; border:2px solid var(--border-3);
                  border-top-color:var(--accent); border-radius:50%; animation:spin 0.7s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }

  .error-state  { display:flex; flex-direction:column; align-items:center; gap:8px;
                  padding:60px 40px; text-align:center; }
  .error-icon   { font-size:32px; color:var(--danger-text); }
  .error-title  { margin:0; font-size:15px; font-weight:600; color:var(--text-3); }
  .error-body   { margin:0; font-size:12px; color:var(--danger-text); }
  .error-hint   { margin:0; font-size:12px; color:var(--text-6); }

  .empty-state  { display:flex; flex-direction:column; align-items:center; gap:10px;
                  padding:80px 40px; text-align:center; }
  .empty-icon   { font-size:40px; }
  .empty-state p { margin:0; font-size:13px; color:var(--text-6); }

  @media (max-width:900px) {
    .panel      { padding:20px 16px; }
    .tables-row { grid-template-columns:1fr; }
  }
</style>
