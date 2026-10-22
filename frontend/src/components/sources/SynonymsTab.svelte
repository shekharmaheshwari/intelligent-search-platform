<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { synonymsStore } from '../../lib/stores/synonyms-store';
  import type { Synonym } from '../../lib/types';

  export let indexId: string;

  $: synonyms = $synonymsStore.synonyms;
  $: loading  = $synonymsStore.loading;
  $: saving   = $synonymsStore.saving;
  $: error    = $synonymsStore.error;

  // New-set form state
  let newWords   = '';
  let oneWay     = false;
  let newRoot    = '';
  let formError  = '';

  onMount(() => synonymsStore.load(indexId));
  onDestroy(() => synonymsStore.reset());

  function slugify(words: string[]): string {
    return words.join('-').toLowerCase().replace(/[^a-z0-9-]+/g, '').slice(0, 60) || `syn-${Date.now()}`;
  }

  async function addSet(): Promise<void> {
    formError = '';
    const words = newWords.split(',').map(w => w.trim()).filter(Boolean);
    if (words.length < 2) {
      formError = 'Enter at least 2 comma-separated words.';
      return;
    }
    if (oneWay && !newRoot.trim()) {
      formError = 'Enter a root word for a one-way synonym, or turn off "One-way".';
      return;
    }
    const id = slugify(words);
    await synonymsStore.upsert(indexId, id, words, oneWay ? newRoot.trim() : undefined);
    if (synonymsStore.getValue().error) {
      formError = synonymsStore.getValue().error;
      return;
    }
    newWords = '';
    newRoot  = '';
    oneWay   = false;
  }

  function removeSet(id: string): void {
    synonymsStore.remove(indexId, id);
  }
</script>

<div class="synonyms-tab">
  <div class="tab-header">
    <div>
      <h2 class="tab-title">Synonyms</h2>
      <p class="tab-desc">
        Treat different words as equivalent during search — e.g. <strong>wire = cable = cord</strong>.
        Applies immediately, no rebuild required. Use <strong>one-way</strong> when the words
        shouldn't be fully interchangeable (searching the root also finds the synonyms, but not
        the reverse).
      </p>
    </div>
  </div>

  <div class="add-form">
    <div class="add-row">
      <input
        class="words-input"
        type="text"
        placeholder="Comma-separated words, e.g. wire, cable, cord"
        bind:value={newWords}
        on:keydown={(e) => { if (e.key === 'Enter' && !oneWay) addSet(); }}
      />
      <label class="one-way-toggle">
        <input type="checkbox" bind:checked={oneWay} />
        One-way
      </label>
      {#if oneWay}
        <input
          class="root-input"
          type="text"
          placeholder="Root word (e.g. tv)"
          bind:value={newRoot}
        />
      {/if}
      <button class="add-btn" on:click={addSet} disabled={saving}>
        {saving ? 'Saving…' : '+ Add'}
      </button>
    </div>
    {#if formError}<p class="form-error">{formError}</p>{/if}
  </div>

  {#if error}
    <div class="alert-error">{error}</div>
  {/if}

  {#if loading}
    <div class="loading">Loading…</div>
  {:else if synonyms.length === 0}
    <div class="empty">
      <p>No synonym sets yet. Add one above to start treating related words as equivalent.</p>
    </div>
  {:else}
    <div class="synonyms-list">
      {#each synonyms as s (s.id)}
        <div class="synonym-card">
          {#if s.root}
            <span class="root-chip">{s.root}</span>
            <span class="arrow">→</span>
            <span class="words">{s.synonyms.join(', ')}</span>
          {:else}
            <span class="words">{s.synonyms.join(' = ')}</span>
          {/if}
          <button class="remove-btn" on:click={() => removeSet(s.id)} title="Delete synonym set">✕</button>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .synonyms-tab { display: flex; flex-direction: column; gap: 20px; }

  .tab-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  .tab-title { font-size: 15px; font-weight: 600; color: var(--text); margin-bottom: 4px; }
  .tab-desc { font-size: 13px; color: var(--text-5); line-height: 1.5; max-width: 620px; }
  .tab-desc strong { color: var(--text-4); }

  .add-form {
    display: flex; flex-direction: column; gap: 6px;
    border: 1px solid var(--border); border-radius: 8px; padding: 14px;
    background: var(--bg-2);
  }
  .add-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

  .words-input, .root-input {
    background: var(--bg-3); border: 1px solid var(--border); border-radius: 6px;
    padding: 7px 10px; font-size: 13px; color: var(--text); font-family: inherit;
  }
  .words-input { flex: 1; min-width: 220px; }
  .root-input { width: 160px; }
  .words-input:focus, .root-input:focus { outline: none; border-color: var(--accent); }

  .one-way-toggle {
    display: flex; align-items: center; gap: 6px;
    font-size: 13px; color: var(--text-4); white-space: nowrap; cursor: pointer;
  }

  .add-btn {
    flex-shrink: 0; background: var(--accent); color: #fff; border: none;
    border-radius: 6px; padding: 7px 16px; font-size: 13px; font-weight: 500;
    cursor: pointer; white-space: nowrap;
  }
  .add-btn:hover { opacity: 0.88; }
  .add-btn:disabled { opacity: 0.5; cursor: default; }

  .form-error { font-size: 12px; color: #f87171; margin: 0; }

  .alert-error {
    background: rgba(220,50,50,.08); border: 1px solid rgba(220,50,50,.3);
    color: #f87171; border-radius: 6px; padding: 10px 14px; font-size: 13px;
  }

  .loading { color: var(--text-5); font-size: 13px; }
  .empty { text-align: center; padding: 40px 0; color: var(--text-5); font-size: 13px; }

  .synonyms-list { display: flex; flex-direction: column; gap: 10px; }

  .synonym-card {
    display: flex; align-items: center; gap: 10px;
    border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 14px; background: var(--bg-2);
  }
  .words { font-size: 13px; color: var(--text); flex: 1; }
  .root-chip {
    font-size: 12px; font-weight: 600; color: var(--accent);
    background: rgba(124,108,244,.12); border-radius: 10px; padding: 2px 9px;
  }
  .arrow { color: var(--text-5); font-size: 13px; }

  .remove-btn {
    background: none; border: none; color: var(--text-5); cursor: pointer;
    font-size: 13px; padding: 2px 6px; flex-shrink: 0; line-height: 1;
    border-radius: 4px;
  }
  .remove-btn:hover { color: #f87171; background: rgba(220,50,50,.08); }
</style>
