<script lang="ts">
  import { onMount } from 'svelte';
  import { indexService } from '../../lib/services/indexes/IndexService';
  import type { GSheetConnectorConfig } from '../../lib/types';

  export let indexId: string;
  export let connector: Record<string, unknown> = {};

  let spreadsheetId = '';
  let sheetName     = 'Sheet1';
  let headerRow     = 1;
  let idCol         = '';
  let titleCol      = '';
  let contentCol    = '';
  let urlCol        = '';
  let skipEmpty     = true;
  let mappings: { key: string; value: string }[] = [];

  let saving = false;
  let err    = '';
  let saved  = false;

  onMount(() => {
    spreadsheetId = String(connector.spreadsheet_id  ?? '');
    sheetName     = String(connector.sheet_name      ?? 'Sheet1');
    headerRow     = Number(connector.header_row      ?? 1);
    idCol         = String(connector.id_column       ?? '');
    titleCol      = String(connector.title_column    ?? '');
    contentCol    = String(connector.content_column  ?? '');
    urlCol        = String(connector.url_column      ?? '');
    skipEmpty     = Boolean(connector.skip_empty_rows ?? true);
    mappings      = Object.entries((connector.field_map ?? {}) as Record<string, string>).map(([k, v]) => ({ key: k, value: v }));
  });

  function addMapping()             { mappings = [...mappings, { key: '', value: '' }]; }
  function removeMapping(i: number) { mappings = mappings.filter((_, idx) => idx !== i); }

  async function save() {
    saving = true; err = ''; saved = false;
    try {
      const cfg: GSheetConnectorConfig = {
        type:            'gsheet',
        spreadsheet_id:  spreadsheetId.trim(),
        sheet_name:      sheetName.trim() || 'Sheet1',
        header_row:      headerRow,
        id_column:       idCol.trim(),
        title_column:    titleCol.trim(),
        content_column:  contentCol.trim(),
        url_column:      urlCol.trim(),
        skip_empty_rows: skipEmpty,
        field_map:       Object.fromEntries(mappings.filter(r => r.key.trim() && r.value.trim()).map(r => [r.key.trim(), r.value.trim()])),
      };
      await indexService.saveGsheetConnector(indexId, cfg);
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
    Spreadsheet ID
    <input bind:value={spreadsheetId} placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms" />
  </label>
  <div class="field-row-2col">
    <label class="field">
      Sheet name <span class="hint-inline">(tab name)</span>
      <input bind:value={sheetName} placeholder="Sheet1" />
    </label>
    <label class="field">
      Header row
      <input type="number" bind:value={headerRow} min="1" />
    </label>
  </div>
  <div class="field-row-2col">
    <label class="field">Title column <span class="hint-inline req">*</span><input bind:value={titleCol} placeholder="e.g. Title" /></label>
    <label class="field">URL column<input bind:value={urlCol} placeholder="url" /></label>
    <label class="field">Content column<input bind:value={contentCol} placeholder="description" /></label>
    <label class="field">ID column <span class="hint-inline">(auto if blank)</span><input bind:value={idCol} /></label>
  </div>

  <div class="field">
    <span class="field-label-text">Extra field renames <span class="hint-inline">(sheet column → ISP field)</span></span>
    {#each mappings as row, i}
      <div class="extra-row">
        <input bind:value={row.key} placeholder="Sheet column" class="field-input" />
        <span class="arrow">→</span>
        <input bind:value={row.value} placeholder="ISP field" class="field-input" />
        <button class="rm-btn" type="button" on:click={() => removeMapping(i)}>✕</button>
      </div>
    {/each}
    <button type="button" class="add-btn" on:click={addMapping}>+ Add rename</button>
  </div>

  <label class="field" style="flex-direction:row;align-items:center;gap:8px">
    <input type="checkbox" bind:checked={skipEmpty} style="accent-color:#7c6cf4" />
    <span>Skip empty rows</span>
  </label>

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
  .req { color: var(--danger-text); }
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
