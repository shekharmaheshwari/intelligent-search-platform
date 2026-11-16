<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  /**
   * ManualDocumentModal — add or edit a hand-entered document.
   *
   * The form is built from the index's collection schema (``/documents/form-schema``):
   * title and content are required, every other field the index carries is
   * optional, and ranking signals sit in a collapsed section. Fields not in
   * the schema yet can be added as custom key/value pairs.
   *
   * Pass ``docId`` to edit an existing manual document; omit it to add one.
   * Emits ``saved`` after a create/update/delete and ``close`` on cancel.
   */
  import { onMount, createEventDispatcher } from 'svelte';
  import { indexService } from '../../lib/services/indexes/IndexService';
  import type { ManualFormField } from '../../lib/types';
  import {
    inputKind, fieldLabel, isMultiline, toFormValues, toPayload, missingRequired, apiErrorMessage,
    type FormValues, type CustomField,
  } from './ManualDocumentModal.helpers';

  export let indexId: string;
  export let docId: string | null = null;

  const dispatch = createEventDispatcher<{ saved: void; close: void }>();

  let fields: ManualFormField[] = [];
  let values: FormValues = {};
  let custom: CustomField[] = [];
  let loading = true;
  let saving = false;
  let confirmDelete = false;
  let err = '';

  $: editable    = fields.filter(f => inputKind(f) !== null);
  $: coreFields  = editable.filter(f => f.group === 'core');
  $: indexFields = editable.filter(f => f.group === 'field');
  $: rankFields  = editable.filter(f => f.group === 'editorial');
  $: missing     = missingRequired(fields, values);

  onMount(async () => {
    try {
      const [schema, stored] = await Promise.all([
        indexService.getManualFormSchema(indexId),
        docId ? indexService.getManualDocument(indexId, docId) : Promise.resolve({}),
      ]);
      fields = schema;
      ({ values, custom } = toFormValues(fields, stored));
    } catch (e) {
      err = apiErrorMessage(e);
    } finally {
      loading = false;
    }
  });

  async function save() {
    if (missing.length) { err = `Required: ${missing.join(', ')}`; return; }
    err = '';
    saving = true;
    try {
      const payload = toPayload(fields, values, custom);
      if (docId) await indexService.updateManualDocument(indexId, docId, payload);
      else       await indexService.createManualDocument(indexId, payload);
      dispatch('saved');
    } catch (e) {
      err = apiErrorMessage(e);
    } finally {
      saving = false;
    }
  }

  async function remove() {
    if (!docId) return;
    saving = true;
    err = '';
    try {
      await indexService.deleteManualDocument(indexId, docId);
      dispatch('saved');
    } catch (e) {
      err = apiErrorMessage(e);
      confirmDelete = false;
    } finally {
      saving = false;
    }
  }

  function addCustom() { custom = [...custom, { name: '', value: '', kind: 'text' }]; }
  function removeCustom(i: number) { custom = custom.filter((_, j) => j !== i); }
  function close() { if (!saving) dispatch('close'); }
</script>

<svelte:window on:keydown={(e) => e.key === 'Escape' && close()} />

<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
<div class="overlay" on:click|self={close}>
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="mdm-title">
    <div class="modal-header">
      <h3 id="mdm-title">{docId ? 'Edit document' : 'Add document'}</h3>
      <button class="btn-close" on:click={close} aria-label="Close">✕</button>
    </div>

    {#if loading}
      <p class="muted">Loading form…</p>
    {:else}
      <div class="body">
        {#each coreFields as f (f.name)}
          {@const kind = inputKind(f)}
          <label class="field">
            <span class="label">{fieldLabel(f.name)}{#if f.required} <span class="req">*</span>{/if}</span>
            {#if kind === 'textarea'}
              <textarea class="input" rows="4" bind:value={values[f.name]}
                placeholder="Text that search and chat will match against"></textarea>
            {:else}
              <input class="input" type="text" bind:value={values[f.name]}
                inputmode={f.name === 'url' ? 'url' : 'text'}
                placeholder={f.name === 'url' ? 'https://…' : ''} />
            {/if}
          </label>
        {/each}

        {#if indexFields.length}
          <div class="section-title">Index fields</div>
          {#each indexFields as f (f.name)}
            {@const kind = inputKind(f)}
            {#if kind === 'checkbox'}
              <label class="check">
                <input type="checkbox" checked={values[f.name] === true}
                  on:change={(e) => (values[f.name] = e.currentTarget.checked)} />
                <span>{fieldLabel(f.name)}</span>
              </label>
            {:else if isMultiline(kind)}
              <label class="field">
                <span class="label">{fieldLabel(f.name)} <span class="type">{f.type}</span></span>
                <textarea class="input" rows="3" bind:value={values[f.name]}
                  placeholder="One per line"></textarea>
              </label>
            {:else}
              <label class="field">
                <span class="label">{fieldLabel(f.name)} <span class="type">{f.type}</span></span>
                <input
                  class="input"
                  type="text"
                  inputmode={kind === 'integer' || kind === 'number' ? 'decimal' : 'text'}
                  bind:value={values[f.name]}
                />
              </label>
            {/if}
          {/each}
        {/if}

        <div class="section-title">
          Custom fields
          <button class="link" on:click={addCustom}>+ Add field</button>
        </div>
        {#each custom as c, i}
          <div class="custom-row">
            <input class="input" placeholder="name" bind:value={c.name} />
            {#if isMultiline(c.kind)}
              <textarea class="input" rows="3" placeholder="One per line" bind:value={c.value}></textarea>
            {:else}
              <input class="input" placeholder={c.kind === 'text' ? 'value' : c.kind} bind:value={c.value} />
            {/if}
            <button class="btn-close" on:click={() => removeCustom(i)} aria-label="Remove field">✕</button>
          </div>
        {/each}

        {#if rankFields.length}
          <details class="advanced">
            <summary>Ranking (optional)</summary>
            {#each rankFields as f (f.name)}
              {@const kind = inputKind(f)}
              {#if kind === 'checkbox'}
                <label class="check">
                  <input type="checkbox" checked={values[f.name] === true}
                  on:change={(e) => (values[f.name] = e.currentTarget.checked)} />
                  <span>{fieldLabel(f.name)}</span>
                </label>
              {:else}
                <label class="field">
                  <span class="label">{fieldLabel(f.name)}</span>
                  {#if isMultiline(kind)}
                    <textarea class="input" rows="3" bind:value={values[f.name]}
                      placeholder="One query per line"></textarea>
                  {:else}
                    <input class="input" type="text" bind:value={values[f.name]} placeholder="0" />
                  {/if}
                </label>
              {/if}
            {/each}
          </details>
        {/if}
      </div>
    {/if}

    {#if err}<p class="err" role="alert">{err}</p>{/if}

    {#if confirmDelete}
      <div class="confirm">
        <span>Delete this document? This cannot be undone.</span>
        <div class="actions-right">
          <button class="btn-cancel" on:click={() => (confirmDelete = false)} disabled={saving}>Cancel</button>
          <button class="btn-danger" on:click={remove} disabled={saving}>{saving ? 'Deleting…' : 'Delete'}</button>
        </div>
      </div>
    {/if}

    <div class="actions">
      {#if docId && !confirmDelete}
        <button class="btn-delete" on:click={() => (confirmDelete = true)} disabled={saving}>Delete</button>
      {:else}<div></div>{/if}
      <div class="actions-right">
        <button class="btn-cancel" on:click={close} disabled={saving}>Cancel</button>
        <button class="btn-save" on:click={save} disabled={saving || loading || missing.length > 0}>
          {saving ? 'Saving…' : docId ? 'Save' : 'Add'}
        </button>
      </div>
    </div>
  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0; background: rgba(0, 0, 0, 0.55);
    display: flex; align-items: center; justify-content: center; z-index: 100;
    padding: 16px;
  }
  .modal {
    background: var(--bg); border: 1px solid var(--border); border-radius: 12px;
    padding: 22px 24px; width: 100%; max-width: 560px; max-height: calc(100vh - 32px);
    display: flex; flex-direction: column; gap: 14px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  }
  .modal-header { display: flex; justify-content: space-between; align-items: center; }
  .modal-header h3 { margin: 0; font-size: 14px; font-weight: 600; color: var(--text-2); }
  .body { display: flex; flex-direction: column; gap: 12px; overflow-y: auto; padding-right: 4px; }
  .field { display: flex; flex-direction: column; gap: 5px; }
  .label { font-size: 12px; color: var(--text-4); }
  .type { font-size: 10px; color: var(--text-5); font-family: ui-monospace, monospace; margin-left: 4px; }
  .req { color: var(--danger-text); }
  .input {
    background: var(--bg-3); border: 1px solid var(--border); border-radius: 6px;
    padding: 7px 10px; color: var(--text-2); font-size: 13px; font-family: inherit;
    width: 100%; box-sizing: border-box;
  }
  .input:focus { outline: none; border-color: var(--accent); }
  textarea.input { resize: vertical; min-height: 80px; }
  .check { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-2); }
  .section-title {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--text-5); padding-top: 6px; border-top: 1px solid var(--border);
  }
  .custom-row { display: grid; grid-template-columns: 1fr 1.5fr auto; gap: 8px; align-items: start; }
  .advanced { border-top: 1px solid var(--border); padding-top: 8px; }
  .advanced summary { font-size: 12px; color: var(--text-4); cursor: pointer; margin-bottom: 10px; }
  .advanced[open] { display: flex; flex-direction: column; gap: 12px; }
  .link {
    background: none; border: none; color: var(--accent); cursor: pointer;
    font-size: 12px; text-transform: none; letter-spacing: 0; font-weight: 500;
  }
  .muted { color: var(--text-5); font-size: 13px; margin: 0; }
  .err { font-size: 12px; color: var(--danger-text); margin: 0; }
  .confirm {
    display: flex; justify-content: space-between; align-items: center; gap: 10px;
    font-size: 12px; color: var(--text-3);
  }
  .actions { display: flex; justify-content: space-between; align-items: center; }
  .actions-right { display: flex; gap: 8px; }
  .btn-close {
    background: none; border: none; color: var(--text-5); font-size: 14px;
    cursor: pointer; padding: 2px 6px; border-radius: 4px;
  }
  .btn-close:hover { color: var(--text-2); }
  .btn-save {
    background: var(--accent); border: none; border-radius: 6px; color: #fff;
    font-size: 12px; font-weight: 600; padding: 7px 18px; cursor: pointer;
  }
  .btn-save:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-save:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-cancel {
    background: none; border: 1px solid var(--border-2); border-radius: 6px;
    color: var(--text-4); font-size: 12px; padding: 7px 16px; cursor: pointer;
  }
  .btn-cancel:hover:not(:disabled) { border-color: var(--text-5); color: var(--text-2); }
  .btn-cancel:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-delete {
    background: none; border: none; color: var(--danger-text); font-size: 12px; cursor: pointer; padding: 7px 0;
  }
  .btn-danger {
    background: var(--danger-text); border: none; border-radius: 6px; color: #fff;
    font-size: 12px; font-weight: 600; padding: 7px 16px; cursor: pointer;
  }
  .btn-danger:disabled, .btn-delete:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
