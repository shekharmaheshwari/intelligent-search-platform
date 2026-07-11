<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { personalisationStore } from '../../lib/stores/personalisation-store';
  import type { AffinityDimension, ViewedItemPolicy } from '../../lib/types';
  import ViewedItemBehaviourPanel from './ViewedItemBehaviourPanel.svelte';
  import { toggleDimension, setDimensionBoost } from './AffinityDimensionsPanel.helpers';

  export let indexId: string;

  let dims: AffinityDimension[] = [];
  let policy: ViewedItemPolicy = { viewed: { mode: 'penalise', value: 0.05 }, purchased: { mode: 'penalise', value: 0.10 } };
  let saved = false;

  $: loading     = $personalisationStore.loading;
  $: saving      = $personalisationStore.saving;
  $: discovering = $personalisationStore.discovering;
  $: err         = $personalisationStore.error;

  onMount(async () => {
    await personalisationStore.load(indexId);
    const s = personalisationStore.getValue();
    dims   = s.dims;
    policy = s.policy;
  });
  onDestroy(() => personalisationStore.reset());

  async function save() {
    saved = false;
    await personalisationStore.save(indexId, dims, policy);
    const s = personalisationStore.getValue();
    if (!s.error) {
      dims   = s.dims;
      policy = s.policy;
      saved  = true;
    }
  }

  async function rediscover() {
    saved = false;
    await personalisationStore.rediscover(indexId);
    const s = personalisationStore.getValue();
    if (!s.error) {
      dims   = s.dims;
      policy = s.policy;
    }
  }

  function onToggle(i: number, e: Event) {
    const checked = (e.currentTarget as HTMLInputElement).checked;
    dims  = toggleDimension(dims, i, checked);
    saved = false;
  }

  function onBoostInput(i: number, e: Event) {
    const updated = setDimensionBoost(dims, i, (e.currentTarget as HTMLInputElement).value);
    if (updated) { dims = updated; saved = false; }
  }


</script>

<div class="panel">
  <div class="panel-head">
    <div>
      <h3>Affinity Dimensions</h3>
      <p class="sub">
        Categorical fields discovered in this index's documents.
        Enable or disable each dimension and tune its boost weight — changes take effect immediately.
        Dimensions are re-discovered automatically after every rebuild.
      </p>
    </div>
    <button class="btn-discover" on:click={rediscover} disabled={discovering || loading}>
      {discovering ? 'Scanning…' : '↺ Re-discover'}
    </button>
  </div>

  {#if err}<p class="err">{err}</p>{/if}

  {#if loading}
    <div class="empty">Scanning index…</div>
  {:else if dims.length === 0}
    <div class="empty">
      No affinity dimensions found. Rebuild this index to trigger discovery,
      or click <strong>Re-discover</strong> to scan the current documents.
    </div>
  {:else}
    <table class="dim-table">
      <thead>
        <tr>
          <th>Enabled</th>
          <th>Field</th>
          <th>Boost weight</th>
          <th>Sample values</th>
        </tr>
      </thead>
      <tbody>
        {#each dims as dim, idx}
          <tr class:row-off={!dim.enabled}>
            <td class="td-toggle">
              <label class="toggle">
                <input
                  type="checkbox"
                  checked={dim.enabled}
                  on:change={e => onToggle(idx, e)}
                />
                <span class="toggle-track"><span class="toggle-thumb"></span></span>
              </label>
            </td>

            <td class="td-field">
              <span class="field-name">{dim.field}</span>
              {#if dim.derived_from}
                <span class="derived-badge">derived from {dim.derived_from}</span>
              {/if}
            </td>

            <td class="td-boost">
              <input
                class="boost-input"
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={dim.boost}
                on:input={e => onBoostInput(idx, e)}
                disabled={!dim.enabled}
              />
            </td>

            <td class="td-values">
              <div class="value-chips">
                {#each (dim.values ?? []).slice(0, 8) as v}
                  <span class="chip">{v}</span>
                {/each}
                {#if (dim.values ?? []).length > 8}
                  <span class="chip chip-more">+{(dim.values ?? []).length - 8} more</span>
                {/if}
              </div>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>

    <ViewedItemBehaviourPanel {policy} onChange={p => { policy = p; saved = false; }} />

    <div class="actions">
      {#if err}<p class="err">{err}</p>{/if}
      {#if saved}<p class="ok">Saved.</p>{/if}
      <button class="btn-save" on:click={save} disabled={saving}>
        {saving ? 'Saving…' : 'Save changes'}
      </button>
    </div>
  {/if}
</div>

<style>
  .panel { display: flex; flex-direction: column; gap: 20px; padding: 24px 0; }

  .panel-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 20px;
  }

  h3 { margin: 0; font-size: 15px; font-weight: 600; color: var(--text-2); }
  .sub { margin: 4px 0 0; font-size: 12px; color: var(--text-5); max-width: 560px; line-height: 1.5; }

  .btn-discover {
    background: var(--bg-2);
    border: 1px solid var(--border-2);
    border-radius: 7px;
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-3);
    cursor: pointer;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .btn-discover:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .btn-discover:disabled { opacity: 0.4; cursor: not-allowed; }

  .empty {
    color: var(--text-5);
    font-size: 13px;
    padding: 32px 0;
    text-align: center;
  }

  .dim-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }

  .dim-table th {
    text-align: left;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-5);
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }

  .dim-table td {
    padding: 12px 12px;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
  }

  .dim-table tr:last-child td { border-bottom: none; }

  .row-off td { opacity: 0.45; }

  .td-toggle { width: 56px; }

  .td-field { min-width: 160px; }

  .field-name {
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-2);
  }

  .derived-badge {
    display: inline-block;
    margin-left: 8px;
    font-size: 10px;
    background: var(--accent-dim);
    color: var(--accent);
    border: 1px solid var(--accent-border);
    border-radius: 4px;
    padding: 1px 5px;
  }

  .td-boost { width: 110px; }

  .boost-input {
    width: 80px;
    background: var(--bg-2);
    border: 1px solid var(--border-2);
    border-radius: 5px;
    padding: 5px 8px;
    font-size: 12px;
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }
  .boost-input:focus { outline: none; border-color: var(--accent); }
  .boost-input:disabled { opacity: 0.4; }

  .td-values { max-width: 340px; }

  .value-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .chip {
    display: inline-block;
    background: var(--bg-2);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 2px 7px;
    font-size: 11px;
    color: var(--text-4);
  }

  .chip-more {
    background: none;
    border-color: var(--border);
    color: var(--text-6);
    font-style: italic;
  }

  .actions {
    display: flex;
    align-items: center;
    gap: 12px;
    padding-top: 4px;
  }

  .btn-save {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 7px;
    padding: 9px 22px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .btn-save:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-save:disabled { opacity: 0.4; cursor: not-allowed; }

  .err { color: var(--danger-text); font-size: 12px; margin: 0; }
  .ok  { color: #5cdb85; font-size: 12px; margin: 0; }


  /* Toggle styles */
  .toggle { display: flex; align-items: center; cursor: pointer; }
  .toggle input { display: none; }

  .toggle-track {
    width: 38px;
    height: 21px;
    background: var(--border-2);
    border-radius: 11px;
    position: relative;
    transition: background 0.2s;
  }
  .toggle input:checked + .toggle-track { background: var(--accent); }

  .toggle-thumb {
    position: absolute;
    top: 2px; left: 2px;
    width: 17px; height: 17px;
    background: #fff;
    border-radius: 50%;
    transition: left 0.2s;
    box-shadow: 0 1px 3px rgba(0,0,0,0.25);
  }
  .toggle input:checked + .toggle-track .toggle-thumb { left: 19px; }
</style>
