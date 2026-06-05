<script lang="ts">
  /**
   * IndexExtensionsPanel — per-index extension assignment overlay.
   *
   * Opened from the "⚙ Extensions" button in SourceContent.  Allows the user to:
   * - See which extensions are assigned to this index and in what order.
   * - Reorder extensions with ↑↓ arrows (swaps ``run_order`` values).
   * - Toggle individual extensions on/off without removing the assignment.
   * - Remove extensions from this index (does not delete the extension from library).
   * - Add available library extensions that are not yet assigned.
   *
   * Dispatches ``close`` when the overlay backdrop or ✕ button is clicked.
   */
  import { onMount, createEventDispatcher } from 'svelte';
  import { api } from '../lib/api';
  import type { IndexExtension, ExtensionMeta } from '../lib/types';

  export let indexId: string;

  const dispatch = createEventDispatcher<{ close: void }>();

  let assigned: IndexExtension[] = [];
  let library: ExtensionMeta[] = [];
  let loading = true;
  let errMsg = '';

  /** Fetch both the assigned extensions and the full library in parallel, then refresh state. */
  async function load() {
    loading = true; errMsg = '';
    try {
      [assigned, library] = await Promise.all([
        api.listIndexExtensions(indexId),
        api.listExtensions(),
      ]);
    } catch (e) {
      errMsg = String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  // Derived sets — recomputed whenever assigned or library changes
  $: assignedIds = new Set(assigned.map(e => e.id));
  $: available = library.filter(e => !assignedIds.has(e.id));

  /** Add a library extension to this index at the end of the current run_order sequence. */
  async function add(ext: ExtensionMeta) {
    const order = assigned.length;
    try {
      await api.assignExtension(indexId, ext.id, order, true);
      await load();
    } catch (e) { errMsg = String(e); }
  }

  /** Remove (unassign) an extension from this index. Does not delete it from the library. */
  async function remove(ext: IndexExtension) {
    try {
      await api.unassignExtension(indexId, ext.id);
      await load();
    } catch (e) { errMsg = String(e); }
  }

  /** Toggle an extension between enabled and disabled without changing its run_order. */
  async function toggle(ext: IndexExtension) {
    try {
      await api.assignExtension(indexId, ext.id, ext.run_order, !ext.enabled);
      await load();
    } catch (e) { errMsg = String(e); }
  }

  /**
   * Swap this extension one position earlier in the run_order sequence.
   * Exchanges ``run_order`` values with the preceding item so the list stays contiguous.
   */
  async function moveUp(idx: number) {
    if (idx === 0) return;
    const a = assigned[idx - 1];
    const b = assigned[idx];
    await Promise.all([
      api.assignExtension(indexId, a.id, b.run_order, a.enabled),
      api.assignExtension(indexId, b.id, a.run_order, b.enabled),
    ]);
    await load();
  }

  /**
   * Swap this extension one position later in the run_order sequence.
   * Exchanges ``run_order`` values with the following item so the list stays contiguous.
   */
  async function moveDown(idx: number) {
    if (idx === assigned.length - 1) return;
    const a = assigned[idx];
    const b = assigned[idx + 1];
    await Promise.all([
      api.assignExtension(indexId, a.id, b.run_order, a.enabled),
      api.assignExtension(indexId, b.id, a.run_order, b.enabled),
    ]);
    await load();
  }
</script>

<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
<div class="overlay" on:click|self={() => dispatch('close')} role="dialog" aria-modal="true">
  <div class="panel">
    <div class="panel-head">
      <div>
        <h3>Extensions</h3>
        <p class="sub">JS transforms applied before indexing, in the order shown.</p>
      </div>
      <button class="close-btn" on:click={() => dispatch('close')}>✕</button>
    </div>

    {#if errMsg}<p class="err">{errMsg}</p>{/if}

    {#if loading}
      <div class="loading"><div class="spinner"></div><span>Loading…</span></div>
    {:else}
      <!-- Assigned extensions -->
      <div class="section-label">Applied to this index <span class="count">{assigned.length}</span></div>

      {#if assigned.length === 0}
        <div class="empty-assigned">No extensions assigned. Add one from the library below.</div>
      {:else}
        <div class="assigned-list">
          {#each assigned as ext, i}
            <div class="assigned-row" class:disabled={!ext.enabled}>
              <div class="order-btns">
                <button class="order-btn" on:click={() => moveUp(i)} disabled={i === 0} aria-label="Move up">↑</button>
                <button class="order-btn" on:click={() => moveDown(i)} disabled={i === assigned.length - 1} aria-label="Move down">↓</button>
              </div>
              <div class="ext-info">
                <span class="ext-name">{ext.name}</span>
                {#if ext.description}<span class="ext-desc">{ext.description}</span>{/if}
              </div>
              <div class="row-actions">
                <button
                  class="toggle-btn"
                  class:on={ext.enabled}
                  on:click={() => toggle(ext)}
                  title={ext.enabled ? 'Disable' : 'Enable'}
                >{ext.enabled ? 'On' : 'Off'}</button>
                <button class="rm-btn" on:click={() => remove(ext)} title="Remove">✕</button>
              </div>
            </div>
          {/each}
        </div>
      {/if}

      <!-- Extension library -->
      <div class="section-label" style="margin-top:20px">
        Extension library <span class="count">{available.length}</span>
      </div>

      {#if library.length === 0}
        <div class="empty-assigned">No extensions in library yet. Create one in the Extensions page.</div>
      {:else if available.length === 0}
        <div class="empty-assigned">All library extensions are already assigned.</div>
      {:else}
        <div class="library-list">
          {#each available as ext}
            <div class="library-row">
              <div class="ext-info">
                <span class="ext-name">{ext.name}</span>
                {#if ext.description}<span class="ext-desc">{ext.description}</span>{/if}
              </div>
              <button class="add-btn" on:click={() => add(ext)}>+ Add</button>
            </div>
          {/each}
        </div>
      {/if}
    {/if}
  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.75);
    display: flex; align-items: center; justify-content: center;
    z-index: 300;
  }

  .panel {
    background: #111;
    border: 1px solid #222;
    border-radius: 12px;
    width: 520px;
    max-width: 96vw;
    max-height: 80vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .panel-head {
    display: flex; justify-content: space-between; align-items: flex-start;
    padding: 18px 20px 14px;
    border-bottom: 1px solid #1a1a1a;
    flex-shrink: 0;
    gap: 12px;
  }
  h3 { margin: 0; font-size: 15px; font-weight: 600; color: #eee; }
  .sub { margin: 3px 0 0; font-size: 12px; color: #555; }
  .close-btn { background: none; border: none; color: #555; cursor: pointer; font-size: 15px; flex-shrink: 0; }

  .loading { display: flex; align-items: center; gap: 10px; color: #555; font-size: 13px; padding: 24px 20px; }
  .spinner {
    width: 16px; height: 16px; border: 2px solid #222; border-top-color: #7c6cf4;
    border-radius: 50%; animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .err { color: #e55; font-size: 12px; margin: 0; padding: 0 20px; }

  .section-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px;
    color: #444; padding: 14px 20px 6px; display: flex; align-items: center; gap: 6px;
    flex-shrink: 0;
  }
  .count {
    background: #181818; border-radius: 8px; padding: 1px 6px;
    font-size: 10px; color: #3a3a3a;
  }

  .assigned-list, .library-list {
    overflow-y: auto;
    padding: 0 20px 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .empty-assigned {
    padding: 8px 20px 12px;
    font-size: 12px;
    color: #3a3a3a;
  }

  /* Assigned row */
  .assigned-row {
    display: flex;
    align-items: center;
    gap: 10px;
    background: #0f0f0f;
    border: 1px solid #1e1e1e;
    border-radius: 7px;
    padding: 9px 12px;
    transition: opacity 0.15s;
  }
  .assigned-row.disabled { opacity: 0.45; }

  .order-btns { display: flex; flex-direction: column; gap: 1px; flex-shrink: 0; }
  .order-btn {
    background: none; border: none; color: #444; cursor: pointer;
    font-size: 11px; line-height: 1; padding: 1px 3px; border-radius: 3px;
  }
  .order-btn:hover:not(:disabled) { color: #9d91f5; background: #1e1b3a; }
  .order-btn:disabled { opacity: 0.2; cursor: not-allowed; }

  .ext-info { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
  .ext-name { font-size: 13px; font-weight: 500; color: #ccc; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ext-desc { font-size: 11px; color: #4a4a4a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .row-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }

  .toggle-btn {
    font-size: 11px; font-weight: 600; border-radius: 5px;
    padding: 3px 8px; cursor: pointer; border: 1px solid;
    transition: background 0.1s;
  }
  .toggle-btn.on { background: #0f1f16; border-color: #1e3d29; color: #5cdb85; }
  .toggle-btn:not(.on) { background: #1a1a1a; border-color: #2a2a2a; color: #444; }
  .toggle-btn.on:hover { background: #142b1e; }
  .toggle-btn:not(.on):hover { background: #222; color: #777; }

  .rm-btn {
    background: none; border: none; color: #444; cursor: pointer;
    font-size: 12px; padding: 3px 5px; border-radius: 4px;
  }
  .rm-btn:hover { background: #2a0f0f; color: #e55; }

  /* Library row */
  .library-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border: 1px solid #191919;
    border-radius: 7px;
    background: #0a0a0a;
  }

  .add-btn {
    background: #1a1a2e; border: 1px solid #2e2b55; color: #9d91f5;
    border-radius: 6px; padding: 5px 10px; font-size: 12px; cursor: pointer;
    flex-shrink: 0; white-space: nowrap;
  }
  .add-btn:hover { background: #2a2552; }
</style>
