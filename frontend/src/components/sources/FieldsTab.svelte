<script lang="ts">
  import { onMount } from 'svelte';
  import { indexService } from '../../lib/services/indexes/IndexService';
  import type { FieldEntry } from '../../lib/types';

  export let indexId: string;

  let fields: FieldEntry[] = [];
  let loading = true;
  let loadError = '';

  // Per-row: field name → which prop is saving / errored
  let saving: Record<string, string> = {};
  let rowError: Record<string, string> = {};

  const ALWAYS_SEARCHABLE = new Set(['title', 'content']);

  onMount(async () => {
    try {
      const res = await indexService.getFields(indexId);
      fields = res.fields;
    } catch (e: unknown) {
      loadError = e instanceof Error ? e.message : 'Failed to load fields';
    } finally {
      loading = false;
    }
  });

  async function toggle(field: FieldEntry, prop: 'facet' | 'multi_value' | 'searchable') {
    if (field.system) return;
    if (prop === 'searchable' && ALWAYS_SEARCHABLE.has(field.name)) return;

    const key = `${field.name}:${prop}`;
    saving = { ...saving, [key]: '1' };
    rowError = { ...rowError, [field.name]: '' };

    const patch: Partial<{ facet: boolean; multi_value: boolean; searchable: boolean }> = {
      [prop]: !field[prop],
    };

    try {
      const updated = await indexService.updateField(indexId, field.name, patch);
      fields = fields.map(f => f.name === field.name ? updated : f);
    } catch (e: unknown) {
      rowError = { ...rowError, [field.name]: e instanceof Error ? e.message : 'Update failed' };
    } finally {
      const next = { ...saving };
      delete next[key];
      saving = next;
    }
  }

  function isBusy(fieldName: string, prop: string): boolean {
    return !!saving[`${fieldName}:${prop}`];
  }

  function typeLabel(type: string): string {
    const map: Record<string, string> = {
      'string': 'string', 'string[]': 'string[ ]',
      'int32': 'int32', 'int64': 'int64',
      'float': 'float', 'float[]': 'float[ ]',
      'bool': 'bool',
    };
    return map[type] ?? type;
  }
</script>

<div class="fields-tab">
  <div class="tab-header">
    <div>
      <h2 class="tab-title">Fields</h2>
      <p class="tab-desc">
        Configure search and filter behaviour per field. Facet and Multi-value changes
        update the Typesense schema immediately. Searchable takes effect on the next search.
        Changes persist across rebuilds.
      </p>
    </div>
  </div>

  {#if loading}
    <div class="state-msg">Loading fields…</div>
  {:else if loadError}
    <div class="state-msg err">{loadError}</div>
  {:else if fields.length === 0}
    <div class="state-msg">
      No fields yet. Run a rebuild to populate the schema.
    </div>
  {:else}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th class="col-name">Field</th>
            <th class="col-type">Type</th>
            <th class="col-toggle" title="Include in query_by for BM25/hybrid text search">Searchable</th>
            <th class="col-toggle" title="Enable facet counts for filtering">Facet</th>
            <th class="col-toggle" title="Store as an array of values (string[])">Multi-value</th>
          </tr>
        </thead>
        <tbody>
          {#each fields as f (f.name)}
            <tr class:system-row={f.system}>
              <td class="col-name">
                <span class="field-name">{f.name}</span>
                {#if f.system}
                  <span class="badge system">system</span>
                {:else if ALWAYS_SEARCHABLE.has(f.name)}
                  <span class="badge fixed">always searchable</span>
                {/if}
              </td>
              <td class="col-type">
                <code class="type-chip">{typeLabel(f.type)}</code>
              </td>

              <!-- Searchable -->
              <td class="col-toggle">
                {#if f.system}
                  <span class="dash">—</span>
                {:else}
                  <button
                    class="toggle"
                    class:on={f.searchable}
                    class:loading={isBusy(f.name, 'searchable')}
                    disabled={f.system || ALWAYS_SEARCHABLE.has(f.name) || isBusy(f.name, 'searchable')}
                    title={ALWAYS_SEARCHABLE.has(f.name) ? 'Always searchable — cannot disable' : ''}
                    on:click={() => toggle(f, 'searchable')}
                    aria-label="{f.searchable ? 'Disable' : 'Enable'} searchable for {f.name}"
                  >
                    {#if isBusy(f.name, 'searchable')}
                      <span class="spinner"></span>
                    {:else}
                      <span class="knob"></span>
                    {/if}
                  </button>
                {/if}
              </td>

              <!-- Facet -->
              <td class="col-toggle">
                {#if f.system}
                  <span class="dash">—</span>
                {:else}
                  <button
                    class="toggle"
                    class:on={f.facet}
                    class:loading={isBusy(f.name, 'facet')}
                    disabled={f.system || isBusy(f.name, 'facet')}
                    on:click={() => toggle(f, 'facet')}
                    aria-label="{f.facet ? 'Disable' : 'Enable'} facet for {f.name}"
                  >
                    {#if isBusy(f.name, 'facet')}
                      <span class="spinner"></span>
                    {:else}
                      <span class="knob"></span>
                    {/if}
                  </button>
                {/if}
              </td>

              <!-- Multi-value -->
              <td class="col-toggle">
                {#if f.system || !f.type.startsWith('string')}
                  <span class="dash">—</span>
                {:else}
                  <button
                    class="toggle"
                    class:on={f.multi_value}
                    class:loading={isBusy(f.name, 'multi_value')}
                    disabled={f.system || isBusy(f.name, 'multi_value')}
                    on:click={() => toggle(f, 'multi_value')}
                    aria-label="{f.multi_value ? 'Disable' : 'Enable'} multi-value for {f.name}"
                  >
                    {#if isBusy(f.name, 'multi_value')}
                      <span class="spinner"></span>
                    {:else}
                      <span class="knob"></span>
                    {/if}
                  </button>
                {/if}
              </td>
            </tr>
            {#if rowError[f.name]}
              <tr class="err-row">
                <td colspan="5" class="err-cell">{rowError[f.name]}</td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .fields-tab { display: flex; flex-direction: column; gap: 20px; }

  .tab-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }

  .tab-title {
    font-size: 15px; font-weight: 700; color: var(--text); margin: 0 0 4px;
  }
  .tab-desc {
    font-size: 12px; color: var(--text-5); margin: 0; line-height: 1.6;
  }

  .state-msg {
    text-align: center; padding: 48px 0;
    font-size: 13px; color: var(--text-5);
  }
  .state-msg.err { color: var(--danger-text); }

  .table-wrap {
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
  }

  table {
    width: 100%; border-collapse: collapse;
    font-size: 12.5px;
  }

  thead {
    background: var(--bg-2);
    border-bottom: 1px solid var(--border);
  }
  th {
    padding: 9px 14px;
    font-size: 11px; font-weight: 600;
    color: var(--text-5);
    text-transform: uppercase; letter-spacing: 0.5px;
    text-align: left;
  }
  th.col-toggle { text-align: center; }

  tbody tr {
    border-bottom: 1px solid var(--border);
    transition: background 0.1s;
  }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover:not(.system-row):not(.err-row) { background: var(--bg-2); }

  .system-row { opacity: 0.5; }

  td { padding: 10px 14px; vertical-align: middle; }
  td.col-toggle { text-align: center; }

  .col-name { width: 220px; }
  .col-type { width: 120px; }
  .col-toggle { width: 100px; }

  .field-name {
    font-weight: 500; color: var(--text-2);
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 12px;
  }

  .badge {
    display: inline-block;
    font-size: 10px; font-weight: 600;
    border-radius: 4px; padding: 2px 6px;
    margin-left: 6px; vertical-align: middle;
  }
  .badge.system  { background: var(--bg-3); color: var(--text-5); }
  .badge.fixed   { background: var(--accent-faint, #fce4f0); color: var(--accent); }

  .type-chip {
    background: var(--bg-3);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 2px 7px;
    font-size: 11px;
    color: var(--text-4);
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
  }

  .dash { color: var(--text-5); font-size: 14px; }

  /* Toggle switch */
  .toggle {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: flex-start;
    width: 36px; height: 20px;
    border-radius: 10px;
    border: none;
    background: var(--border-3, #d1d5db);
    cursor: pointer;
    padding: 2px;
    transition: background 0.18s;
    flex-shrink: 0;
  }
  .toggle.on  { background: var(--accent); justify-content: flex-end; }
  .toggle:disabled { cursor: not-allowed; opacity: 0.5; }
  .toggle:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  .knob {
    display: block;
    width: 16px; height: 16px;
    border-radius: 50%;
    background: #fff;
    box-shadow: 0 1px 3px rgba(0,0,0,0.2);
    transition: none;
  }

  /* Spinner inside toggle */
  .toggle.loading { cursor: wait; }
  .spinner {
    display: block;
    width: 12px; height: 12px;
    border: 2px solid rgba(255,255,255,0.4);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    margin: auto;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .err-row td { padding: 0 14px 8px; }
  .err-cell {
    font-size: 11px; color: var(--danger-text);
    background: var(--danger-bg, #fef2f2);
    border-radius: 5px; padding: 5px 10px;
  }
</style>
