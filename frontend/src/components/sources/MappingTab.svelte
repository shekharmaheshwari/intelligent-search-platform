<script lang="ts">
  // MappingTab — four independent mapping sections (intent label, field renames, value overrides,
  // static field values) for a single index.  Transient per-component state only; calls
  // indexService directly.  Does not read from any store; parent passes connector and intentLabel as props.

  import { onMount } from 'svelte';
  import { indexService } from '../../lib/services/indexes/IndexService';
  import type { MappingConfig } from '../../lib/types';

  export let indexId:    string;
  export let connector:  Record<string, unknown> = {};
  export let intentLabel: string | null = null;

  // ── Local mutable state ───────────────────────────────────────────────────

  /** Working copy of the intent label text input. */
  let labelValue = '';

  /** Flat rows for the field_map section: source key → target key. */
  let fieldRows: { key: string; value: string }[] = [];

  /**
   * Flat rows for the value_map section.
   * The nested ``{field: {from: to}}`` structure is flattened on mount and
   * re-nested before each save.
   */
  let valueRows: { field: string; from: string; to: string }[] = [];

  // ── Per-section save state (each section saves independently) ─────────────

  let savingLabel  = false;
  let errLabel     = '';
  let savedLabel   = false;

  let savingFields = false;
  let errFields    = '';
  let savedFields  = false;

  let savingValues = false;
  let errValues    = '';
  let savedValues  = false;

  /** Flat rows for the static_fields section: constant key stamped into every document. */
  let staticFields: { key: string; value: string }[] = [];

  let staticSaving = false;
  let staticErr    = '';
  let staticSaved  = false;

  // ── Initialise from props on mount ────────────────────────────────────────

  onMount(() => {
    labelValue = intentLabel ?? '';

    // field_map is a flat Record<string, string>; convert to editable rows.
    const fieldMap = (connector.field_map ?? {}) as Record<string, string>;
    fieldRows = Object.entries(fieldMap).map(([k, v]) => ({ key: k, value: v }));

    // value_map is Record<string, Record<string, string>>; flatten to {field, from, to} rows.
    const valueMap = (connector.value_map ?? {}) as Record<string, Record<string, string>>;
    valueRows = Object.entries(valueMap).flatMap(([field, inner]) =>
      Object.entries(inner).map(([from, to]) => ({ field, from, to }))
    );

    // static_fields is a flat Record<string, string>; convert to editable rows.
    const staticMap = (connector.static_fields ?? {}) as Record<string, string>;
    staticFields = Object.entries(staticMap).map(([k, v]) => ({ key: k, value: v }));
  });

  // ── Field-rename row helpers ───────────────────────────────────────────────

  function addField()             { fieldRows = [...fieldRows, { key: '', value: '' }]; }
  function removeField(i: number) { fieldRows = fieldRows.filter((_, idx) => idx !== i); }

  // ── Value-override row helpers ────────────────────────────────────────────

  function addValue()             { valueRows = [...valueRows, { field: '', from: '', to: '' }]; }
  function removeValue(i: number) { valueRows = valueRows.filter((_, idx) => idx !== i); }

  // ── Static-field row helpers ──────────────────────────────────────────────

  function addStatic()             { staticFields = [...staticFields, { key: '', value: '' }]; }
  function removeStatic(i: number) { staticFields = staticFields.filter((_, idx) => idx !== i); }

  // ── Build maps from current rows ──────────────────────────────────────────

  /**
   * Build the ``field_map`` object from the current ``fieldRows`` state.
   * Rows with an empty key are skipped; empty value means "drop the field".
   */
  function buildFieldMap(): Record<string, string> {
    return Object.fromEntries(
      fieldRows
        .filter(r => r.key.trim())
        .map(r => [r.key.trim(), r.value.trim()])
    );
  }

  /**
   * Build the nested ``value_map`` object from the current flat ``valueRows``.
   * Rows with an empty field or from-value are skipped.
   * Multiple rows with the same field are merged into one inner map.
   */
  function buildValueMap(): Record<string, Record<string, string>> {
    const result: Record<string, Record<string, string>> = {};
    for (const row of valueRows) {
      if (!row.field.trim() || !row.from.trim()) continue;
      const f = row.field.trim();
      if (!result[f]) result[f] = {};
      result[f][row.from.trim()] = row.to.trim();
    }
    return result;
  }

  /**
   * Build the ``static_fields`` object from the current ``staticFields`` state.
   * Rows with an empty key are skipped.
   */
  function buildStaticFields(): Record<string, string> {
    return Object.fromEntries(
      staticFields
        .filter(r => r.key.trim())
        .map(r => [r.key.trim(), r.value.trim()])
    );
  }

  // ── Save handlers ─────────────────────────────────────────────────────────

  /** Save intent label only; does not touch field_map or value_map. */
  async function saveIntentLabel() {
    savingLabel = true; errLabel = ''; savedLabel = false;
    try {
      await indexService.saveIntentLabel(indexId, labelValue.trim());
      savedLabel = true;
      // Auto-clear the success indicator after 2 s so it does not persist indefinitely.
      setTimeout(() => { savedLabel = false; }, 2000);
    } catch (e) {
      errLabel = String(e);
    } finally {
      savingLabel = false;
    }
  }

  /**
   * Save field renames.
   * Includes value_map and static_fields state so saving renames does not clobber other sections.
   */
  async function saveFieldRenames() {
    savingFields = true; errFields = ''; savedFields = false;
    try {
      const cfg: MappingConfig = {
        field_map:     buildFieldMap(),
        value_map:     buildValueMap(),     // preserve value overrides when saving only renames
        static_fields: buildStaticFields(), // preserve static fields when saving only renames
      };
      await indexService.saveMapping(indexId, cfg);
      savedFields = true;
      setTimeout(() => { savedFields = false; }, 2000);
    } catch (e) {
      errFields = String(e);
    } finally {
      savingFields = false;
    }
  }

  /**
   * Save value overrides.
   * Includes field_map and static_fields state so saving overrides does not clobber other sections.
   */
  async function saveValueOverrides() {
    savingValues = true; errValues = ''; savedValues = false;
    try {
      const cfg: MappingConfig = {
        field_map:     buildFieldMap(),     // preserve field renames when saving only overrides
        value_map:     buildValueMap(),
        static_fields: buildStaticFields(), // preserve static fields when saving only overrides
      };
      await indexService.saveMapping(indexId, cfg);
      savedValues = true;
      setTimeout(() => { savedValues = false; }, 2000);
    } catch (e) {
      errValues = String(e);
    } finally {
      savingValues = false;
    }
  }

  /**
   * Save static field values.
   * Includes field_map and value_map state so saving static fields does not clobber other sections.
   */
  async function saveStaticFields() {
    staticSaving = true; staticErr = ''; staticSaved = false;
    try {
      const cfg: MappingConfig = {
        field_map:     buildFieldMap(),     // preserve field renames when saving only static fields
        value_map:     buildValueMap(),     // preserve value overrides when saving only static fields
        static_fields: buildStaticFields(),
      };
      await indexService.saveMapping(indexId, cfg);
      staticSaved = true;
      // Auto-clear the success indicator after 2 s so it does not persist indefinitely.
      setTimeout(() => { staticSaved = false; }, 2000);
    } catch (e) {
      staticErr = String(e);
    } finally {
      staticSaving = false;
    }
  }
</script>

<!-- ── Section 1: Intent Label ──────────────────────────────────────────── -->
<div class="setting-card">
  <div class="section-heading">Intent Label</div>
  <label class="field">
    Intent label
    <input bind:value={labelValue} placeholder="device, accessory, smartwatch, tablet…" />
    <span class="hint">
      Scopes typeahead suggestions and tab intent detection. Leave blank if this index
      does not power a PLP tab.
    </span>
  </label>
  {#if errLabel}<p class="err">{errLabel}</p>{/if}
  <div class="actions">
    <button class="btn-save" on:click={saveIntentLabel} disabled={savingLabel}>
      {savingLabel ? 'Saving…' : savedLabel ? '✓ Saved' : 'Save'}
    </button>
  </div>
</div>

<!-- ── Section 2: Field Renames ─────────────────────────────────────────── -->
<div class="setting-card">
  <div class="section-heading">Field Renames</div>
  <p class="section-desc">Rename source fields before indexing. Set the target to empty to drop the field entirely.</p>

  <div class="field">
    {#each fieldRows as row, i}
      <div class="mapping-row">
        <input
          bind:value={row.key}
          placeholder="source field"
          class="field-input"
        />
        <span class="arrow">→</span>
        <input
          bind:value={row.value}
          placeholder="target field"
          class="field-input"
        />
        <button class="rm-btn" type="button" on:click={() => removeField(i)}>✕</button>
      </div>
    {/each}
    <button type="button" class="add-btn" on:click={addField}>+ Add rename</button>
  </div>

  <p class="rebuild-notice">Rebuild required to apply to existing documents.</p>
  {#if errFields}<p class="err">{errFields}</p>{/if}
  <div class="actions">
    <button class="btn-save" on:click={saveFieldRenames} disabled={savingFields}>
      {savingFields ? 'Saving…' : savedFields ? '✓ Saved' : 'Save'}
    </button>
  </div>
</div>

<!-- ── Section 3: Value Overrides ───────────────────────────────────────── -->
<div class="setting-card">
  <div class="section-heading">Value Overrides</div>
  <p class="section-desc">Rewrite field values after field renames have been applied.</p>

  <div class="field">
    {#if valueRows.length > 0}
      <!-- Column headers only shown when there is at least one row. -->
      <div class="value-header">
        <span class="col-label">Field</span>
        <span class="col-label">Raw value</span>
        <span class="col-arrow"></span>
        <span class="col-label">Stored as</span>
        <span class="col-rm"></span>
      </div>
    {/if}
    {#each valueRows as row, i}
      <div class="mapping-row">
        <input
          bind:value={row.field}
          placeholder="category"
          class="field-input"
        />
        <input
          bind:value={row.from}
          placeholder="accessories"
          class="field-input"
        />
        <span class="arrow">→</span>
        <input
          bind:value={row.to}
          placeholder="accessory"
          class="field-input"
        />
        <button class="rm-btn" type="button" on:click={() => removeValue(i)}>✕</button>
      </div>
    {/each}
    <button type="button" class="add-btn" on:click={addValue}>+ Add override</button>
  </div>

  <p class="rebuild-notice">Rebuild required to apply to existing documents.</p>
  {#if errValues}<p class="err">{errValues}</p>{/if}
  <div class="actions">
    <button class="btn-save" on:click={saveValueOverrides} disabled={savingValues}>
      {savingValues ? 'Saving…' : savedValues ? '✓ Saved' : 'Save'}
    </button>
  </div>
</div>

<!-- ── Section 4: Static Field Values ───────────────────────────────────── -->
<div class="setting-card">
  <div class="section-heading">Static Field Values</div>
  <p class="section-desc">
    Injected into every document at ingest time, regardless of source data.
    Use this to stamp a category, source label, or any fixed value onto all documents.
  </p>

  <div class="field">
    {#each staticFields as row, i}
      <div class="mapping-row">
        <input
          bind:value={row.key}
          placeholder="field name (e.g. category)"
          class="field-input"
        />
        <span class="arrow">→</span>
        <input
          bind:value={row.value}
          placeholder="constant value (e.g. account)"
          class="field-input"
        />
        <button class="rm-btn" type="button" on:click={() => removeStatic(i)}>✕</button>
      </div>
    {/each}
    <button type="button" class="add-btn" on:click={addStatic}>+ Add field</button>
  </div>

  <p class="rebuild-notice">Rebuild required to apply to existing documents.</p>
  {#if staticErr}<p class="err">{staticErr}</p>{/if}
  <div class="actions">
    <button class="btn-save" on:click={saveStaticFields} disabled={staticSaving}>
      {staticSaving ? 'Saving…' : staticSaved ? '✓ Saved' : 'Save'}
    </button>
  </div>
</div>

<style>
  .setting-card {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 11px;
    margin-bottom: 16px;
  }

  .section-heading {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--text-5);
  }

  .section-desc {
    margin: 0;
    font-size: 12px;
    color: var(--text-5);
  }

  /* Generic label+input field, mirrors SitemapConnectorTab pattern. */
  .field {
    display: flex;
    flex-direction: column;
    gap: 5px;
    font-size: 12px;
    color: var(--text-4);
  }

  .field input {
    background: var(--bg-3);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 7px 10px;
    color: var(--text-2);
    font-size: 12px;
    font-family: inherit;
  }

  .field input:focus { outline: none; border-color: var(--accent); }

  .hint {
    font-size: 11px;
    color: var(--text-5);
    margin-top: 2px;
  }

  /* Row layout shared by field-rename and value-override rows. */
  .mapping-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
  }

  .field-input {
    flex: 1;
    background: var(--bg-3);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 9px;
    color: var(--text-2);
    font-size: 12px;
    font-family: inherit;
  }

  .field-input:focus { outline: none; border-color: var(--accent); }

  .arrow { color: var(--text-5); font-size: 12px; flex-shrink: 0; }

  /* Column header row for the value overrides section. */
  .value-header {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
  }

  .col-label {
    flex: 1;
    font-size: 10px;
    font-weight: 600;
    color: var(--text-5);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  /* Spacer elements that align with the arrow and remove button. */
  .col-arrow { width: 14px; flex-shrink: 0; }
  .col-rm    { width: 28px; flex-shrink: 0; }

  .rm-btn {
    background: none;
    border: 1px solid var(--border-3);
    border-radius: 5px;
    color: var(--text-5);
    font-size: 11px;
    padding: 4px 7px;
    cursor: pointer;
    flex-shrink: 0;
  }

  .rm-btn:hover { border-color: var(--danger-border); color: var(--danger-text); }

  .add-btn {
    background: none;
    border: 1px dashed var(--border-3);
    border-radius: 5px;
    color: var(--text-5);
    font-size: 11px;
    padding: 5px 12px;
    cursor: pointer;
    margin-top: 2px;
    align-self: flex-start;
  }

  .add-btn:hover { border-color: var(--accent); color: var(--accent); }

  .rebuild-notice {
    margin: 0;
    font-size: 11px;
    color: var(--text-5);
    font-style: italic;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    padding-top: 4px;
  }

  .btn-save {
    background: var(--accent);
    border: none;
    border-radius: 6px;
    color: #fff;
    font-size: 12px;
    font-weight: 600;
    padding: 6px 18px;
    cursor: pointer;
  }

  .btn-save:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-save:disabled { opacity: 0.4; cursor: not-allowed; }

  .err {
    color: var(--danger-text) !important;
    font-size: 12px;
    margin: 6px 0 0;
  }
</style>
