<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  export let connector: Record<string, unknown> = {};

  $: url          = String(connector.url          ?? '');
  $: method       = String(connector.method       ?? 'GET');
  $: resultPath   = String(connector.result_path  ?? '');
  $: idField      = String(connector.id_field     ?? 'id');
  $: titleField   = String(connector.title_field  ?? 'title');
  $: contentField = String(connector.content_field ?? 'content');
  $: urlField     = String(connector.url_field    ?? 'url');
  $: fieldMap     = Object.entries((connector.field_map ?? {}) as Record<string, string>)
                      .map(([k, v]) => ({ key: k, value: v }));
</script>

<div class="setting-card">
  <div class="info-row">
    <span class="info-label">Source URL</span>
    <span class="info-val">{url || '—'}</span>
  </div>
  <div class="info-row">
    <span class="info-label">Method</span>
    <span class="info-val">{method}</span>
  </div>
  {#if resultPath}
  <div class="info-row">
    <span class="info-label">Result path</span>
    <span class="info-val mono">{resultPath}</span>
  </div>
  {/if}
  <div class="fields-grid">
    <div class="info-row">
      <span class="info-label">ID field</span>
      <span class="info-val mono">{idField}</span>
    </div>
    <div class="info-row">
      <span class="info-label">Title field</span>
      <span class="info-val mono">{titleField}</span>
    </div>
    <div class="info-row">
      <span class="info-label">Content field</span>
      <span class="info-val mono">{contentField}</span>
    </div>
    <div class="info-row">
      <span class="info-label">URL field</span>
      <span class="info-val mono">{urlField}</span>
    </div>
  </div>
  {#if fieldMap.length > 0}
  <div>
    <span class="info-label">Field renames</span>
    <div class="field-map">
      {#each fieldMap as row}
        <span class="mono">{row.key}</span>
        <span class="arrow">→</span>
        <span class="mono">{row.value}</span>
      {/each}
    </div>
  </div>
  {/if}
  <p class="hint">To change URL, method, or headers — use the Rebuild panel.</p>
</div>

<style>
  .setting-card {
    background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px;
    display: flex; flex-direction: column; gap: 11px;
  }
  .info-row { display: flex; gap: 12px; align-items: baseline; }
  .info-label { font-size: 12px; color: var(--text-5); min-width: 100px; flex-shrink: 0; }
  .info-val { font-size: 13px; color: var(--text-2); word-break: break-all; }
  .mono { font-family: monospace; font-size: 12px; color: var(--text-3); }
  .fields-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .field-map {
    display: grid; grid-template-columns: auto 16px auto; gap: 4px 8px;
    margin-top: 6px; align-items: center;
  }
  .arrow { color: var(--text-5); font-size: 12px; }
  .hint { font-size: 11px; color: var(--text-5); margin: 4px 0 0; font-style: italic; }
</style>
