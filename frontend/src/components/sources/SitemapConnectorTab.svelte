<script lang="ts">
  import { onMount } from 'svelte';
  import { indexService } from '../../lib/services/indexes/IndexService';
  import type { PatternRow, MetaRow } from './SitemapConnectorTab.helpers';
  import {
    initSitemapFromConnector, initIncludePatterns, initExcludePatterns,
    initMetaFields, buildSitemapConfig,
  } from './SitemapConnectorTab.helpers';

  export let indexId: string;
  export let connector: Record<string, unknown> = {};

  let url             = '';
  let maxPages        = 50;
  let selector        = '';
  let userAgent       = '';
  let includePatterns: PatternRow[] = [];
  let excludePatterns: PatternRow[] = [];
  let metaFields:      MetaRow[]    = [];

  let saving = false;
  let err    = '';
  let saved  = false;

  onMount(() => {
    const s     = initSitemapFromConnector(connector);
    url         = s.url;
    maxPages    = s.maxPages;
    selector    = s.selector;
    userAgent   = s.userAgent;
    includePatterns = initIncludePatterns(connector);
    excludePatterns = initExcludePatterns(connector);
    metaFields      = initMetaFields(connector);
  });

  function addInclude()             { includePatterns = [...includePatterns, { value: '' }]; }
  function removeInclude(i: number) { includePatterns = includePatterns.filter((_, idx) => idx !== i); }
  function addExclude()             { excludePatterns = [...excludePatterns, { value: '' }]; }
  function removeExclude(i: number) { excludePatterns = excludePatterns.filter((_, idx) => idx !== i); }
  function addMeta()                { metaFields = [...metaFields, { key: '', value: '' }]; }
  function removeMeta(i: number)    { metaFields = metaFields.filter((_, idx) => idx !== i); }

  async function save() {
    saving = true; err = ''; saved = false;
    try {
      const cfg = buildSitemapConfig(
        { url, maxPages, selector, userAgent },
        includePatterns, excludePatterns, metaFields,
      );
      await indexService.saveSitemapConnector(indexId, cfg);
      saved = true;
      setTimeout(() => { saved = false; }, 2000);
    } catch (e) {
      err = String(e);
    } finally {
      saving = false;
    }
  }
</script>

<div class="setting-card">
  <label class="field">
    Sitemap URL
    <input bind:value={url} placeholder="https://example.com/sitemap.xml" />
  </label>
  <div class="field-row-2col">
    <label class="field">
      Max pages <span class="hint-inline">(optional, default 50)</span>
      <input type="number" bind:value={maxPages} min="1" max="10000" />
    </label>
    <label class="field">
      Content selector <span class="hint-inline">(optional)</span>
      <input bind:value={selector} placeholder="article, .post-content" />
    </label>
  </div>
  <label class="field">
    User Agent <span class="hint-inline">(optional)</span>
    <input bind:value={userAgent} placeholder="Mozilla/5.0 ..." />
  </label>

  <div class="field">
    <span class="field-label-text">Include patterns <span class="hint-inline">(optional)</span></span>
    {#each includePatterns as row, i}
      <div class="extra-row">
        <input bind:value={row.value} placeholder="/support/" class="field-input" />
        <button class="rm-btn" type="button" on:click={() => removeInclude(i)}>✕</button>
      </div>
    {/each}
    <button type="button" class="add-btn" on:click={addInclude}>+ Add include pattern</button>
  </div>

  <div class="field">
    <span class="field-label-text">Exclude patterns <span class="hint-inline">(optional)</span></span>
    {#each excludePatterns as row, i}
      <div class="extra-row">
        <input bind:value={row.value} placeholder="/tag/" class="field-input" />
        <button class="rm-btn" type="button" on:click={() => removeExclude(i)}>✕</button>
      </div>
    {/each}
    <button type="button" class="add-btn" on:click={addExclude}>+ Add exclude pattern</button>
  </div>

  <div class="field">
    <span class="field-label-text">Meta field mappings <span class="hint-inline">(optional)</span></span>
    {#each metaFields as row, i}
      <div class="extra-row">
        <input bind:value={row.key} placeholder="og:site_name" class="field-input" />
        <span class="arrow">→</span>
        <input bind:value={row.value} placeholder="site_name" class="field-input" />
        <button class="rm-btn" type="button" on:click={() => removeMeta(i)}>✕</button>
      </div>
    {/each}
    <button type="button" class="add-btn" on:click={addMeta}>+ Add field</button>
  </div>

  {#if err}<p class="err">{err}</p>{/if}
  <div class="actions">
    <button class="btn-save" on:click={save} disabled={saving}>
      {saving ? 'Saving…' : saved ? '✓ Saved' : 'Save connector'}
    </button>
  </div>
</div>

<style>
  .setting-card {
    background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px;
    display: flex; flex-direction: column; gap: 11px;
  }
  .field-row-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .field { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--text-4); }
  .field input {
    background: var(--bg-3); border: 1px solid var(--border); border-radius: 6px;
    padding: 7px 10px; color: var(--text-2); font-size: 12px; font-family: inherit;
  }
  .field input:focus { outline: none; border-color: var(--accent); }
  .hint-inline { font-size: 10px; color: var(--text-5); font-weight: 400; }
  .field-label-text { display: block; font-size: 12px; color: var(--text-4); margin-bottom: 4px; }
  .extra-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
  .field-input {
    flex: 1; background: var(--bg-3); border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 9px; color: var(--text-2); font-size: 12px; font-family: inherit;
  }
  .field-input:focus { outline: none; border-color: var(--accent); }
  .arrow { color: var(--text-5); font-size: 12px; }
  .rm-btn {
    background: none; border: 1px solid var(--border-3); border-radius: 5px;
    color: var(--text-5); font-size: 11px; padding: 4px 7px; cursor: pointer; flex-shrink: 0;
  }
  .rm-btn:hover { border-color: var(--danger-border); color: var(--danger-text); }
  .add-btn {
    background: none; border: 1px dashed var(--border-3); border-radius: 5px;
    color: var(--text-5); font-size: 11px; padding: 5px 12px; cursor: pointer; margin-top: 2px;
  }
  .add-btn:hover { border-color: var(--accent); color: var(--accent); }
  .actions { display: flex; justify-content: flex-end; padding-top: 4px; }
  .btn-save {
    background: var(--accent); border: none; border-radius: 6px;
    color: #fff; font-size: 12px; font-weight: 600; padding: 6px 18px; cursor: pointer;
  }
  .btn-save:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-save:disabled { opacity: 0.4; cursor: not-allowed; }
  .err { color: var(--danger-text) !important; font-size: 12px; margin: 6px 0 0; }
</style>
