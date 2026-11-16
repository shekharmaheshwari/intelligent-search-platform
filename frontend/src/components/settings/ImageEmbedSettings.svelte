<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  /**
   * ImageEmbedSettings — global image embedding model for image search.
   *
   * Independent of the text embedding model above it: text search keeps its own
   * model and vectors. "Off" disables image search on every index. After a
   * change, indexes with image search on must re-index their images.
   */
  import { onMount } from 'svelte';
  import { settingsService } from '../../lib/services/settings/SettingsService';
  import type { ImageEmbedSettings } from '../../lib/types';
  import { apiDetail } from '../sources/imageSearch.helpers';

  let data: ImageEmbedSettings | null = null;
  let selected = '';
  let saving = false;
  let saved = false;
  let err = '';

  $: dirty = !!data && selected !== data.model_id;
  $: option = data?.available_models.find(m => m.id === selected);

  onMount(async () => {
    try {
      data = await settingsService.getImageEmbedSettings();
      selected = data.model_id;
    } catch (e) {
      err = apiDetail(e);
    }
  });

  async function save() {
    saving = true;
    saved = false;
    err = '';
    try {
      const prev = data?.model_id ?? '';
      data = await settingsService.setImageEmbedModel(selected);
      selected = data.model_id;
      saved = prev !== data.model_id;
    } catch (e) {
      err = apiDetail(e);
    } finally {
      saving = false;
    }
  }
</script>

<section class="section image-section">
  <div class="section-header">
    <h3>Image Search</h3>
    <p class="section-sub">
      Model used to embed product images and shoppers' photos. Separate from the text
      embedding model — text search is not affected. Turn image search on per index
      in its Overview tab.
    </p>
  </div>

  {#if data}
    <div class="field-group">
      <label class="field-label" for="image-model-select">Image embedding model</label>
      <div class="select-row">
        <select id="image-model-select" class="model-select" bind:value={selected} disabled={saving}>
          <option value="">Off</option>
          {#each data.available_models as m}
            <option value={m.id} disabled={!m.configured}>
              {m.label} — {m.num_dim}d{m.configured ? '' : ' (API key missing)'}
            </option>
          {/each}
        </select>
        <button class="btn-save" on:click={save} disabled={!dirty || saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
      {#if option}<p class="hint">{option.description}</p>{/if}
      {#if saved}
        <p class="ok">
          {#if data.model_id}Image model updated. Re-index images on each index that uses image search.
          {:else}Image search is now off on every index.{/if}
        </p>
      {/if}
    </div>
  {:else if !err}
    <p class="hint">Loading…</p>
  {/if}
  {#if err}<p class="err">{err}</p>{/if}
</section>

<style>
  .section { display: flex; flex-direction: column; gap: 16px; }
  .image-section { margin-top: 32px; padding-top: 28px; border-top: 1px solid var(--border-2); }
  .section-header { display: flex; flex-direction: column; gap: 4px; }
  h3 { margin: 0; font-size: 15px; font-weight: 600; color: var(--text-2); }
  .section-sub { margin: 0; font-size: 12px; color: var(--text-5); }
  .field-group { display: flex; flex-direction: column; gap: 10px; }
  .field-label { font-size: 12px; color: var(--text-3); font-weight: 500; }
  .select-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .model-select {
    flex: 1; min-width: 220px;
    background: var(--bg-3); border: 1px solid var(--border-2); border-radius: 7px;
    padding: 8px 10px; color: var(--text-2); font-size: 13px; font-family: inherit;
  }
  .btn-save {
    background: var(--accent); border: none; border-radius: 7px; color: #fff;
    font-size: 12px; font-weight: 600; padding: 8px 18px; cursor: pointer;
  }
  .btn-save:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-save:disabled { opacity: 0.5; cursor: not-allowed; }
  .hint { margin: 0; font-size: 12px; color: var(--text-5); }
  .ok { margin: 0; font-size: 12px; color: var(--text-3); }
  .err { margin: 0; font-size: 12px; color: var(--danger-text); }
</style>
