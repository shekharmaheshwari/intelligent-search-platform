<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  /**
   * ImageSearchCard — per-index image search settings (Overview tab).
   *
   * Turns image search on/off, picks the field holding each product's image URL
   * (suggested from the indexed docs), shows the last image-indexing run, and
   * re-runs it. Polls while a run is in progress. The image model itself is
   * chosen globally in Settings → Image Search.
   */
  import { onMount, onDestroy } from 'svelte';
  import { indexService } from '../../lib/services/indexes/IndexService';
  import type { ImageSearchState } from '../../lib/types';
  import { describeImageRun, apiDetail } from './imageSearch.helpers';

  export let indexId: string;

  let state: ImageSearchState | null = null;
  let field = '';
  let saving = false;
  let err = '';
  let timer: ReturnType<typeof setTimeout> | null = null;

  $: running = state?.last_run?.state === 'running';
  $: fieldDirty = !!state && field.trim() !== state.field;

  async function load() {
    try {
      state = await indexService.getImageSearch(indexId);
      if (!fieldDirty || !field) field = state.field || state.candidates[0]?.field || '';
      err = '';
    } catch (e) {
      err = apiDetail(e);
    }
    schedulePoll();
  }

  function schedulePoll() {
    if (timer) clearTimeout(timer);
    // Keep polling between passes when another one is queued.
    timer = state?.last_run?.state === 'running' || state?.queued ? setTimeout(load, 2000) : null;
  }

  async function save(enabled: boolean) {
    saving = true;
    err = '';
    try {
      state = await indexService.saveImageSearch(indexId, enabled, field.trim());
      if (enabled) {
        // Indexing starts in the background; show it as running until the first poll.
        state = { ...state, last_run: { ...(state.last_run ?? {}), state: 'running' } };
      }
      schedulePoll();
    } catch (e) {
      err = apiDetail(e);
    } finally {
      saving = false;
    }
  }

  async function reindex() {
    saving = true;
    err = '';
    try {
      await indexService.reindexImages(indexId);
      if (state) state = { ...state, last_run: { ...(state.last_run ?? {}), state: 'running' } };
      schedulePoll();
    } catch (e) {
      err = apiDetail(e);
    } finally {
      saving = false;
    }
  }

  onMount(load);
  onDestroy(() => { if (timer) clearTimeout(timer); });
</script>

<div class="setting-card">
  <div class="setting-header">
    <div class="setting-title-group">
      <span class="setting-title">Image search</span>
      <span class="setting-hint">
        Lets shoppers search with a photo. Product images are embedded with the image
        model chosen in Settings → Image Search, separately from text search.
      </span>
    </div>
    {#if state}
      <div class="setting-current">
        <label class="toggle-switch" aria-label="Toggle image search">
          <input
            type="checkbox"
            checked={state.enabled}
            disabled={saving || (!state.enabled && (!state.model_available || !field.trim()))}
            on:change={(e) => save(e.currentTarget.checked)}
          />
          <span class="toggle-slider"></span>
        </label>
        <span class="toggle-label">{state.enabled ? 'On' : 'Off'}</span>
      </div>
    {/if}
  </div>

  {#if !state}
    {#if !err}<p class="muted">Loading…</p>{/if}
  {:else if !state.model}
    <p class="note">Select an image embedding model in <strong>Settings → Image Search</strong> to turn this on.</p>
  {:else}
    {#if !state.model_available}
      <p class="note warn">The image model <code>{state.model}</code> is missing its API key.</p>
    {/if}

    <div class="field-row">
      <label class="field-label" for="img-field-{indexId}">Image URL field</label>
      <input
        id="img-field-{indexId}"
        class="field-input"
        list="img-field-options-{indexId}"
        bind:value={field}
        placeholder={state.candidates.length ? 'Pick a suggested field' : 'e.g. images'}
        disabled={saving}
      />
      <datalist id="img-field-options-{indexId}">
        {#each state.candidates as c}
          <option value={c.field}>{Math.round(c.coverage * 100)}% of sampled docs</option>
        {/each}
      </datalist>
      {#if state.enabled && fieldDirty}
        <button class="btn-edit" on:click={() => save(true)} disabled={saving || !field.trim()}>Save &amp; re-index</button>
      {/if}
    </div>
    {#if !state.candidates.length && !state.field}
      <p class="hint">No field with image URLs was detected in the indexed documents.</p>
    {/if}

    {#if state.enabled || state.last_run}
      <div class="status-row">
        <span class="status" class:error={state.last_run?.state === 'error'}>
          {#if running}<span class="spinner" aria-hidden="true"></span>{/if}
          {describeImageRun(state.last_run)}
          {#if running && state.queued}<span class="queued">· will run again with the latest settings</span>{/if}
        </span>
        {#if state.enabled && !running}
          <button class="btn-edit" on:click={reindex} disabled={saving}>Re-index images</button>
        {/if}
      </div>
      {#if state.stale && state.last_run?.model}
        <p class="note warn">Images were indexed with <code>{state.last_run?.model}</code>; re-index for <code>{state.model}</code>.</p>
      {/if}
    {/if}
  {/if}

  {#if err}<p class="err">{err}</p>{/if}
</div>

<style>
  .setting-card {
    background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px;
    display: flex; flex-direction: column; gap: 12px;
  }
  .setting-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
  .setting-title-group { display: flex; flex-direction: column; gap: 3px; }
  .setting-title { font-size: 13px; font-weight: 600; color: var(--text-2); }
  .setting-hint { font-size: 12px; color: var(--text-5); max-width: 480px; line-height: 1.5; }
  .setting-current { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  .toggle-label { font-size: 12px; color: var(--text-4); }
  .toggle-switch { position: relative; display: inline-block; width: 34px; height: 18px; }
  .toggle-switch input { opacity: 0; width: 0; height: 0; }
  .toggle-slider {
    position: absolute; inset: 0; cursor: pointer; border-radius: 18px;
    background: var(--border-2); transition: background 0.2s;
  }
  .toggle-slider::before {
    content: ''; position: absolute; height: 14px; width: 14px; left: 2px; top: 2px;
    background: #fff; border-radius: 50%; transition: transform 0.2s;
  }
  .toggle-switch input:checked + .toggle-slider { background: var(--accent); }
  .toggle-switch input:checked + .toggle-slider::before { transform: translateX(16px); }
  .toggle-switch input:disabled + .toggle-slider { opacity: 0.5; cursor: not-allowed; }
  .field-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .field-label { font-size: 12px; color: var(--text-4); }
  .field-input {
    flex: 1; min-width: 160px; max-width: 280px;
    background: var(--bg-3); border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 10px; color: var(--text-2); font-size: 13px; font-family: inherit;
  }
  .field-input:focus { outline: none; border-color: var(--accent); }
  .status-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
  .status { font-size: 12px; color: var(--text-4); display: inline-flex; align-items: center; gap: 6px; }
  .status.error { color: var(--danger-text); }
  .queued { color: var(--text-5); }
  .spinner {
    width: 10px; height: 10px; border: 2px solid var(--border-2); border-top-color: var(--accent);
    border-radius: 50%; animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .btn-edit {
    background: none; border: 1px solid var(--border-2); border-radius: 6px;
    color: var(--text-4); font-size: 12px; padding: 4px 12px; cursor: pointer;
  }
  .btn-edit:hover:not(:disabled) { border-color: var(--text-5); color: var(--text-2); }
  .btn-edit:disabled { opacity: 0.5; cursor: not-allowed; }
  .muted, .hint { font-size: 12px; color: var(--text-5); margin: 0; }
  .note { font-size: 12px; color: var(--text-4); margin: 0; }
  .note.warn { color: var(--danger-text); }
  .err { color: var(--danger-text); font-size: 12px; margin: 0; }
  code { font-size: 11px; }
</style>
