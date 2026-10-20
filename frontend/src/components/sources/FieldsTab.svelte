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

  // Mirrors chat/service.py's _CONCEPT_FIELD_CANDIDATES keys — the backend
  // is the source of truth and rejects anything else, so a mismatch here
  // just surfaces as a save error rather than silently doing the wrong thing.
  // Fixed, small list (7 concepts) — rendered as its own panel keyed by
  // concept rather than a column on every field row, since at most a
  // handful of an index's fields will ever be one of these.
  const CONCEPTS: { value: string; label: string; hint: string }[] = [
    { value: 'price',                label: 'Price',                hint: '"cheapest", "most expensive"' },
    { value: 'popularity',           label: 'Popularity',           hint: '"most popular", "best selling"' },
    { value: 'recency',              label: 'Recency',              hint: '"newest", "latest"' },
    { value: 'size',                 label: 'Size',                 hint: '"biggest", "smallest"' },
    { value: 'rating',               label: 'Rating',               hint: '"top rated"' },
    { value: 'device_compatibility', label: 'Device compatibility', hint: 'e.g. "iPhone 17 case"' },
    { value: 'customer_eligibility', label: 'Customer eligibility', hint: '"cheapest military plan"' },
    { value: 'unit_count',           label: 'Quantity offered',     hint: '"plan for 3 lines", "room for 2 guests"' },
    { value: 'unit_price',           label: 'Price per quantity',   hint: 'aligned with Quantity offered' },
    { value: 'feature_list',         label: 'Feature list',         hint: '"cheapest plan with Netflix for 2 lines"' },
  ];
  const SORT_CONCEPTS = new Set(['price', 'popularity', 'recency', 'size', 'rating']);
  // Quantity concepts map to numeric ARRAY fields (mirrors backend
  // concept_resolution.UNIT_CONCEPTS / NUMERIC_ARRAY_TYPES).
  const UNIT_CONCEPTS = new Set(['unit_count', 'unit_price']);

  // Candidate fields per concept, narrowed by shape so a 30-field index
  // doesn't dump every field into every dropdown: sort concepts need a
  // numeric field to sort by; the two filter concepts need a faceted
  // categorical field to filter by. Mirrors the backend's own
  // _SORTABLE_TYPES check and how device_compatibility/customer_eligibility
  // actually resolve (a facet field, never a number).
  function candidateFieldsFor(concept: string): FieldEntry[] {
    if (UNIT_CONCEPTS.has(concept)) {
      return fields.filter(f => !f.system && ['int32[]', 'int64[]', 'float[]'].includes(f.type));
    }
    if (SORT_CONCEPTS.has(concept)) {
      return fields.filter(f => !f.system && ['int32', 'int64', 'float'].includes(f.type));
    }
    return fields.filter(f => !f.system && f.facet && (f.type === 'string' || f.type === 'string[]'));
  }

  // Per-concept saving/error state — separate from the per-field `saving`/
  // `rowError` above since a concept row isn't keyed by a single field.
  let savingConcepts: Record<string, string> = {};
  let conceptErrors: Record<string, string> = {};

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

  async function toggle(field: FieldEntry, prop: 'facet' | 'multi_value' | 'searchable' | 'chat') {
    if (field.system) return;
    if (prop === 'searchable' && ALWAYS_SEARCHABLE.has(field.name)) return;

    const key = `${field.name}:${prop}`;
    saving = { ...saving, [key]: '1' };
    rowError = { ...rowError, [field.name]: '' };

    const patch: Partial<{ facet: boolean; multi_value: boolean; searchable: boolean; chat: boolean }> = {
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

  function onConceptFieldChange(e: Event, concept: string) {
    setConceptField(concept, (e.currentTarget as HTMLSelectElement).value);
  }

  async function setConceptField(concept: string, fieldName: string) {
    savingConcepts = { ...savingConcepts, [concept]: '1' };
    conceptErrors = { ...conceptErrors, [concept]: '' };

    try {
      if (fieldName) {
        await indexService.updateField(indexId, fieldName, { concept });
      } else {
        // Clearing — find whichever field currently holds this concept.
        // Nothing to do if none does (already cleared, or never set).
        const current = fields.find(f => f.concept === concept);
        if (current) await indexService.updateField(indexId, current.name, { concept: '' });
      }
      // Assigning a concept to a new field silently un-assigns it from
      // whichever field held it before (superlative_field_map is keyed by
      // concept, so a concept can only point at one field) — that changes a
      // field OTHER than the one just patched, so patching one local entry
      // isn't enough; refetch the whole list instead.
      const res = await indexService.getFields(indexId);
      fields = res.fields;
    } catch (e: unknown) {
      conceptErrors = { ...conceptErrors, [concept]: e instanceof Error ? e.message : 'Update failed' };
    } finally {
      const next = { ...savingConcepts };
      delete next[concept];
      savingConcepts = next;
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
        Configure search and filter behaviour per field. Changes persist across rebuilds.
      </p>
    </div>
  </div>

  <dl class="legend">
    <div class="legend-item">
      <dt>Searchable</dt>
      <dd>Included when matching what a customer typed. Turn on for text a shopper might search by.</dd>
    </div>
    <div class="legend-item">
      <dt>Facet</dt>
      <dd>Shown as a filter customers can check in the sidebar (e.g. brand, color). Takes effect immediately.</dd>
    </div>
    <div class="legend-item">
      <dt>Multi-value</dt>
      <dd>This field holds a list, not one value (e.g. several colors) — turn on so each value filters separately.</dd>
    </div>
    <div class="legend-item">
      <dt>Chat</dt>
      <dd>Shown to the AI assistant as a labelled fact it can answer questions from.</dd>
    </div>
  </dl>

  {#if loading}
    <div class="state-msg">Loading fields…</div>
  {:else if loadError}
    <div class="state-msg err">{loadError}</div>
  {:else if fields.length === 0}
    <div class="state-msg">
      No fields yet. Run a rebuild to populate the schema.
    </div>
  {:else}
    <div class="concepts-card">
      <div class="concepts-head">
        <h3>Concepts</h3>
        <p>
          Tag which field on this index answers each concept the chat assistant can sort or
          filter by — e.g. "cheapest X" resolves the Price concept's field. Set once per index.
        </p>
      </div>
      {#each CONCEPTS as c (c.value)}
        {@const candidates = candidateFieldsFor(c.value)}
        {@const currentField = fields.find(f => f.concept === c.value)?.name ?? ''}
        <div class="concept-row">
          <div>
            <div class="concept-name">{c.label}</div>
            <div class="concept-hint">{c.hint}</div>
          </div>
          <select
            class="concept-select"
            class:tagged={!!currentField}
            disabled={!!savingConcepts[c.value]}
            value={currentField}
            on:change={(e) => onConceptFieldChange(e, c.value)}
            aria-label="Field for {c.label}"
          >
            <option value="">— none —</option>
            {#each candidates as f (f.name)}
              <option value={f.name}>{f.name}</option>
            {/each}
          </select>
        </div>
        {#if conceptErrors[c.value]}
          <div class="concept-err">{conceptErrors[c.value]}</div>
        {/if}
      {/each}
    </div>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th class="col-name">Field</th>
            <th class="col-type">Type</th>
            <th class="col-toggle">Searchable</th>
            <th class="col-toggle">Facet</th>
            <th class="col-toggle">Multi-value</th>
            <th class="col-toggle">Chat</th>
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
                    disabled={f.system || ALWAYS_SEARCHABLE.has(f.name) || !f.type.startsWith('string') || isBusy(f.name, 'searchable')}
                    title={ALWAYS_SEARCHABLE.has(f.name) ? 'Always searchable — cannot disable' : !f.type.startsWith('string') ? `${f.type} fields cannot be made searchable` : ''}
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

              <!-- Chat -->
              <td class="col-toggle">
                {#if f.system}
                  <span class="dash">—</span>
                {:else if ALWAYS_SEARCHABLE.has(f.name)}
                  <span class="badge fixed">always</span>
                {:else}
                  <button
                    class="toggle"
                    class:on={f.chat}
                    class:loading={isBusy(f.name, 'chat')}
                    disabled={isBusy(f.name, 'chat')}
                    on:click={() => toggle(f, 'chat')}
                    aria-label="{f.chat ? 'Remove' : 'Add'} {f.name} from chat context"
                  >
                    {#if isBusy(f.name, 'chat')}
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
                <td colspan="6" class="err-cell">{rowError[f.name]}</td>
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

  /* Always-visible column legend — replaces hover-only header tooltips,
     which a user has no way to discover without already knowing to hover. */
  .legend {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 10px 16px;
    margin: 0;
    padding: 12px 14px;
    background: var(--bg-2);
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  .legend-item dt {
    font-size: 11px; font-weight: 700; color: var(--text-2);
    text-transform: uppercase; letter-spacing: 0.5px;
    margin-bottom: 2px;
  }
  .legend-item dd {
    font-size: 11.5px; color: var(--text-5); line-height: 1.5; margin: 0;
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

  /* Concepts panel — 7 fixed rows keyed by concept, not one column per
     field row (most fields are never a concept; see FieldsTab history). */
  .concepts-card {
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    margin-bottom: 4px;
  }
  .concepts-head {
    padding: 12px 14px 10px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-2);
  }
  .concepts-head h3 {
    font-size: 11px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase;
    color: var(--text-2); margin: 0 0 4px;
  }
  .concepts-head p {
    font-size: 11.5px; color: var(--text-5); margin: 0; line-height: 1.5;
  }
  .concept-row {
    display: flex; align-items: center; justify-content: space-between;
    gap: 16px; padding: 10px 14px; border-bottom: 1px solid var(--border);
  }
  .concept-row:last-child { border-bottom: none; }
  .concept-row:hover { background: var(--bg-2); }
  .concept-name { font-size: 12.5px; font-weight: 600; color: var(--text-2); }
  .concept-hint { font-size: 11px; color: var(--text-5); margin-top: 1px; }
  .concept-err {
    font-size: 11px; color: var(--danger-text);
    background: var(--danger-bg, #fef2f2);
    padding: 5px 14px; margin: -1px 0 0;
  }

  .concept-select {
    min-width: 180px;
    font-size: 11.5px;
    font-family: inherit;
    color: var(--text-4);
    background: var(--bg-2);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 4px 6px;
    cursor: pointer;
  }
  .concept-select.tagged {
    color: var(--accent);
    background: var(--accent-faint, #fce4f0);
    border-color: var(--accent);
    font-weight: 600;
  }
  .concept-select:disabled { cursor: wait; opacity: 0.6; }
  .concept-select:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }

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
