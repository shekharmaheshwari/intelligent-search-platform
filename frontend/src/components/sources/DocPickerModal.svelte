<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  import { createEventDispatcher, onMount, onDestroy } from 'svelte';
  import { api } from '../../lib/api';

  let el: HTMLElement;
  onMount(() => { document.body.appendChild(el); });
  onDestroy(() => { el?.remove(); });

  /** Id of the index to search documents within. */
  export let indexId: string;
  /** Doc IDs already selected — used to show the ✓ state on rows. */
  export let selectedIds: string[] = [];

  const dispatch = createEventDispatcher<{ select: { id: string; title: string }; close: void }>();

  let query = '';
  let results: Array<{ id: string; title: string }> = [];
  let loading = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  function onInput(): void {
    if (timer) clearTimeout(timer);
    const q = query.trim();
    if (!q) { results = []; return; }
    timer = setTimeout(async () => {
      loading = true;
      try {
        const res = await api.search(indexId, q, 'lexical', 1, 10);
        results = (res.hits ?? []).map((h: any) => ({
          id:    h.document.id,
          title: h.document.title || h.document.id,
        }));
      } catch { results = []; }
      finally { loading = false; }
    }, 250);
  }

  function select(doc: { id: string; title: string }): void {
    dispatch('select', doc);
  }
</script>

<div bind:this={el} class="picker-backdrop" on:mousedown|self={() => dispatch('close')}>
  <div class="picker">
    <div class="picker-head">
      <span class="picker-title">Browse documents</span>
      <button class="picker-close" on:click={() => dispatch('close')}>✕</button>
    </div>

    <div class="picker-search">
      <input
        class="picker-input"
        type="text"
        bind:value={query}
        placeholder="Search by title or keyword…"
        on:input={onInput}
        autofocus
      />
    </div>

    <div class="picker-results">
      {#if loading}
        <div class="picker-msg">Searching…</div>
      {:else if results.length === 0 && query.trim()}
        <div class="picker-msg">No results found</div>
      {:else if results.length === 0}
        <div class="picker-msg">Type to search documents in this index</div>
      {:else}
        {#each results as doc}
          <button
            class="picker-row"
            class:picker-row-selected={selectedIds.includes(doc.id)}
            on:click={() => select(doc)}
          >
            <span class="picker-row-title">{doc.title}</span>
            <span class="picker-row-id">{doc.id}</span>
            {#if selectedIds.includes(doc.id)}
              <span class="picker-row-check">✓</span>
            {/if}
          </button>
        {/each}
      {/if}
    </div>
  </div>
</div>

<style>
  .picker-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,.55);
    display: flex; align-items: center; justify-content: center; z-index: 500;
  }
  .picker {
    background: var(--bg); border: 1px solid var(--border); border-radius: 12px;
    width: 480px; max-width: 92vw; display: flex; flex-direction: column;
    box-shadow: 0 8px 40px rgba(0,0,0,.4); overflow: hidden;
  }
  .picker-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 16px; border-bottom: 1px solid var(--border);
  }
  .picker-title { font-size: 14px; font-weight: 600; color: var(--text); }
  .picker-close {
    background: none; border: none; color: var(--text-5); cursor: pointer;
    font-size: 14px; padding: 2px 6px;
  }
  .picker-close:hover { color: var(--text); }
  .picker-search { padding: 12px 16px; border-bottom: 1px solid var(--border); }
  .picker-input {
    width: 100%; background: var(--bg-3); border: 1px solid var(--border);
    border-radius: 4px; padding: 7px 10px; font-size: 13px; color: var(--text);
    font-family: inherit; box-sizing: border-box;
  }
  .picker-input:focus { outline: none; border-color: var(--accent); }
  .picker-results { max-height: 320px; overflow-y: auto; background: var(--bg); }
  .picker-msg { padding: 20px; font-size: 13px; color: var(--text-5); text-align: center; }
  .picker-row {
    display: grid; grid-template-columns: 1fr auto; grid-template-rows: auto auto;
    gap: 2px 8px; width: 100%; padding: 10px 16px;
    background: none; border: none; border-bottom: 1px solid var(--border);
    text-align: left; cursor: pointer; align-items: center;
  }
  .picker-row:hover, .picker-row-selected { background: var(--bg-3); }
  .picker-row-title {
    font-size: 13px; color: var(--text); white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; grid-column: 1;
  }
  .picker-row-id {
    font-size: 10px; color: var(--text-5); font-family: monospace;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; grid-column: 1;
  }
  .picker-row-check {
    grid-column: 2; grid-row: 1 / 3; color: var(--accent);
    font-size: 14px; font-weight: 700;
  }
</style>
