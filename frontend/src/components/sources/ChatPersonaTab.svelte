<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { chatPersonaStore } from '../../lib/stores/chat-persona-store';
  import type { ChatPersonaConfig } from '../../lib/types';

  export let indexId: string;

  $: loading = $chatPersonaStore.loading;
  $: saving  = $chatPersonaStore.saving;
  $: error   = $chatPersonaStore.error;

  let itemNoun               = 'product';
  let enableComparison       = true;
  let additionalInstructions = '';
  let saveMsg = '';
  let formError = '';

  const MAX_INSTRUCTIONS_LEN = 2000;

  onMount(async () => {
    await chatPersonaStore.load(indexId);
    const cfg = chatPersonaStore.getValue().config;
    itemNoun               = cfg.item_noun;
    enableComparison       = cfg.enable_comparison;
    additionalInstructions = cfg.additional_instructions;
  });
  onDestroy(() => chatPersonaStore.reset());

  async function save(): Promise<void> {
    formError = '';
    saveMsg   = '';
    const noun = itemNoun.trim();
    if (!noun) {
      formError = 'Item noun cannot be empty.';
      return;
    }
    if (additionalInstructions.length > MAX_INSTRUCTIONS_LEN) {
      formError = `Additional instructions must be under ${MAX_INSTRUCTIONS_LEN} characters.`;
      return;
    }
    const config: ChatPersonaConfig = {
      item_noun:               noun,
      enable_comparison:       enableComparison,
      additional_instructions: additionalInstructions.trim(),
    };
    await chatPersonaStore.save(indexId, config);
    if ($chatPersonaStore.error) {
      formError = $chatPersonaStore.error;
      return;
    }
    saveMsg = 'Saved.';
    setTimeout(() => { saveMsg = ''; }, 2500);
  }
</script>

<div class="chat-persona-tab">
  <div class="tab-header">
    <div>
      <h2 class="tab-title">Chat Persona</h2>
      <p class="tab-desc">
        Controls how the chat assistant talks about this catalog's items — not what it's
        allowed to say. The grounding rule ("answer only from the retrieved data") always
        applies and can't be overridden here. Takes effect on the next chat request — no
        rebuild required.
      </p>
    </div>
  </div>

  {#if error && !formError}
    <div class="alert-error">{error}</div>
  {/if}

  {#if loading}
    <div class="loading">Loading…</div>
  {:else}
    <div class="form">
      <label class="field">
        <span class="field-label">Item noun</span>
        <span class="field-hint">
          What to call one item — fills in phrases like "helpful <strong>{itemNoun || 'product'}</strong> expert assistant."
        </span>
        <input
          class="text-input"
          type="text"
          placeholder="product"
          bind:value={itemNoun}
        />
      </label>

      <label class="toggle-field">
        <input type="checkbox" bind:checked={enableComparison} />
        <span>
          <span class="field-label">Enable comparison language</span>
          <span class="field-hint block">
            Off for catalogs where comparing items side by side doesn't make sense (e.g. documentation).
          </span>
        </span>
      </label>

      <label class="field">
        <span class="field-label">Additional instructions</span>
        <span class="field-hint">
          Optional. Always appended after the fixed rules above — can extend the assistant's
          tone or focus, never replace the grounding rule.
        </span>
        <textarea
          class="textarea-input"
          rows="4"
          maxlength={MAX_INSTRUCTIONS_LEN}
          placeholder="e.g. Mention care difficulty when relevant."
          bind:value={additionalInstructions}
        ></textarea>
        <span class="char-count">{additionalInstructions.length} / {MAX_INSTRUCTIONS_LEN}</span>
      </label>

      {#if formError}<p class="form-error">{formError}</p>{/if}

      <div class="save-row">
        <button class="save-btn" on:click={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        {#if saveMsg}<span class="save-msg">{saveMsg}</span>{/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .chat-persona-tab { display: flex; flex-direction: column; gap: 20px; max-width: 640px; }

  .tab-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  .tab-title { font-size: 15px; font-weight: 600; color: var(--text); margin-bottom: 4px; }
  .tab-desc { font-size: 13px; color: var(--text-5); line-height: 1.5; max-width: 620px; }

  .alert-error {
    background: rgba(220,50,50,.08); border: 1px solid rgba(220,50,50,.3);
    color: #f87171; border-radius: 6px; padding: 10px 14px; font-size: 13px;
  }

  .loading { color: var(--text-5); font-size: 13px; }

  .form {
    display: flex; flex-direction: column; gap: 18px;
    border: 1px solid var(--border); border-radius: 8px; padding: 16px;
    background: var(--bg-2);
  }

  .field { display: flex; flex-direction: column; gap: 6px; }
  .field-label { font-size: 13px; font-weight: 600; color: var(--text); }
  .field-hint { font-size: 12px; color: var(--text-5); line-height: 1.4; }
  .field-hint.block { display: block; margin-top: 2px; }
  .field-hint strong { color: var(--text-4); }

  .text-input, .textarea-input {
    background: var(--bg-3); border: 1px solid var(--border); border-radius: 6px;
    padding: 7px 10px; font-size: 13px; color: var(--text); font-family: inherit;
  }
  .text-input:focus, .textarea-input:focus { outline: none; border-color: var(--accent); }
  .textarea-input { resize: vertical; }

  .toggle-field {
    display: flex; align-items: flex-start; gap: 10px; cursor: pointer;
  }
  .toggle-field input[type="checkbox"] { margin-top: 3px; flex-shrink: 0; }

  .char-count { font-size: 11px; color: var(--text-5); align-self: flex-end; }

  .form-error { font-size: 12px; color: #f87171; margin: 0; }

  .save-row { display: flex; align-items: center; gap: 10px; }
  .save-btn {
    background: var(--accent); color: #fff; border: none;
    border-radius: 6px; padding: 7px 16px; font-size: 13px; font-weight: 500;
    cursor: pointer; white-space: nowrap;
  }
  .save-btn:hover { opacity: 0.88; }
  .save-btn:disabled { opacity: 0.5; cursor: default; }
  .save-msg { font-size: 12px; color: var(--text-5); }
</style>
