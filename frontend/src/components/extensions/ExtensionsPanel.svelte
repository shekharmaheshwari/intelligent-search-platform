<script lang="ts">
  /**
   * ExtensionsPanel — global JS extension library management page.
   *
   * Shown when the user clicks "⚙ Extensions" in the sidebar footer
   * (App.svelte sets ``view = 'extensions'``).
   *
   * Allows developers to create, edit, and delete reusable ``transform(doc)``
   * functions that are applied to documents during ingest/rebuild.
   * Actual per-index assignment is managed in ``IndexExtensionsPanel``.
   */
  import { onMount } from 'svelte';
  import { extensionsStore } from '../../lib/store';
  import type { ExtensionMeta } from '../../lib/types';
  import { fmtDate, filterExtensions } from './ExtensionsPanel.helpers';
  import FilterBar from '../common/FilterBar.svelte';

  $: extensions = $extensionsStore.library;
  $: loading = $extensionsStore.loading;
  $: errMsg = $extensionsStore.error ?? '';

  // Editor modal state
  let showEditor = false;
  let editingId = '';   // empty = create new
  let editName = '';
  let editDesc = '';
  let editCode = '';
  let saving = false;
  let editorErr = '';

  const DEFAULT_CODE = `function transform(doc) {
  // doc contains all fields from your data source.
  // Modify and return it.

  // Example: build URL from a slug field
  // var slug = (doc.slug || '').toLowerCase().replace(/\\s+/g, '-');
  // doc.url = 'https://example.com/products/' + slug;

  // Example: combine two fields into content
  // doc.content = doc.summary + ' ' + doc.body;

  return doc;
}`;

  async function load() {
    await extensionsStore.loadLibrary();
  }

  onMount(load);

  /** Open the editor modal pre-filled with defaults for creating a new extension. */
  function openCreate() {
    editingId = ''; editName = ''; editDesc = ''; editCode = DEFAULT_CODE;
    editorErr = ''; showEditor = true;
  }

  /** Open the editor modal pre-filled with an existing extension's current values. */
  function openEdit(ext: ExtensionMeta) {
    editingId = ext.id; editName = ext.name; editDesc = ext.description; editCode = ext.code;
    editorErr = ''; showEditor = true;
  }

  async function save() {
    if (!editName.trim()) { editorErr = 'Name is required'; return; }
    if (!editCode.trim()) { editorErr = 'Code is required'; return; }
    saving = true; editorErr = '';
    try {
      if (editingId) {
        await extensionsStore.updateExtension(editingId, { name: editName.trim(), description: editDesc.trim(), code: editCode });
      } else {
        await extensionsStore.createExtension(editName.trim(), editDesc.trim(), editCode);
      }
      showEditor = false;
    } catch (e) {
      editorErr = String(e);
    } finally {
      saving = false;
    }
  }

  let filterQuery = '';
  $: filteredExtensions = filterExtensions(extensions, filterQuery);

  let deletingId = '';

  async function deleteExt(id: string) {
    if (!confirm('Delete this extension? Indexes using it will lose the assignment.')) return;
    deletingId = id;
    try { await extensionsStore.deleteExtension(id); }
    catch (e) { errMsg = String(e); }
    finally { deletingId = ''; }
  }
</script>

<div class="panel">
  <div class="panel-head">
    <div>
      <h2>Extensions</h2>
      <p class="panel-sub">JavaScript V8 transforms applied to every document before indexing.</p>
    </div>
    <button class="btn-primary" on:click={openCreate}>+ New extension</button>
  </div>

  {#if errMsg}<p class="err">{errMsg}</p>{/if}

  {#if loading}
    <div class="loading"><div class="spinner"></div><span>Loading…</span></div>
  {:else if extensions.length === 0}
    <div class="empty">
      <div class="empty-icon">⚙</div>
      <p>No extensions yet.</p>
      <p class="empty-sub">
        Create a JavaScript transform to reshape documents before they're indexed —
        rebuild URLs, merge fields, normalise text, and more.
      </p>
      <button class="btn-primary" on:click={openCreate}>Create your first extension</button>
    </div>
  {:else}
    <div class="filter-wrap">
      <FilterBar bind:value={filterQuery} ariaLabel="Filter extensions" />
    </div>

    {#if filteredExtensions.length === 0}
      <div class="empty">
        <p>No matches for "{filterQuery}".</p>
      </div>
    {:else}
    <div class="ext-list">
      {#each filteredExtensions as ext}
        <div class="ext-card">
          <div class="ext-card-head">
            <div class="ext-info">
              <span class="ext-name">{ext.name}</span>
              {#if ext.description}<span class="ext-desc">{ext.description}</span>{/if}
              <span class="ext-date">Created {fmtDate(ext.created_at)}{ext.updated_at !== ext.created_at ? ` · Updated ${fmtDate(ext.updated_at)}` : ''}</span>
            </div>
            <div class="ext-actions">
              <button class="edit-btn" on:click={() => openEdit(ext)}>Edit</button>
              <button class="del-btn" on:click={() => deleteExt(ext.id)} disabled={deletingId === ext.id}>
                {deletingId === ext.id ? '…' : 'Delete'}
              </button>
            </div>
          </div>
          <pre class="code-preview">{ext.code.slice(0, 200)}{ext.code.length > 200 ? '\n…' : ''}</pre>
        </div>
      {/each}
    </div>
    {/if}
  {/if}
</div>

{#if showEditor}
  <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
  <div class="overlay" on:click|self={() => showEditor = false} role="dialog" aria-modal="true">
    <div class="editor-modal">
      <div class="editor-head">
        <h3>{editingId ? 'Edit extension' : 'New extension'}</h3>
        <button class="close-btn" on:click={() => showEditor = false}>✕</button>
      </div>

      <div class="editor-body">
        <label>Name
          <input bind:value={editName} placeholder="URL Rebuilder" />
        </label>
        <label>Description <span class="hint">(optional)</span>
          <input bind:value={editDesc} placeholder="Rebuilds the URL from slug + category" />
        </label>
        <div class="code-label-row">
          <span class="code-label">JavaScript <span class="hint">— must define <code>function transform(doc) &#123; ... return doc; &#125;</code></span></span>
        </div>
        <textarea
          class="code-editor"
          bind:value={editCode}
          rows="18"
          spellcheck="false"
          autocomplete="off"
          autocorrect="off"
          autocapitalize="off"
        ></textarea>

        {#if editorErr}<p class="err">{editorErr}</p>{/if}
        <div class="editor-actions">
          <button class="btn-secondary" on:click={() => showEditor = false}>Cancel</button>
          <button class="btn-primary" on:click={save} disabled={saving}>
            {saving ? 'Saving…' : (editingId ? 'Save changes' : 'Create extension')}
          </button>
        </div>
      </div>
    </div>
  </div>
{/if}

<style>
  .panel {
    padding: 32px 36px 48px;
    max-width: 820px;
  }

  .panel-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    margin-bottom: 28px;
  }
  h2 { margin: 0; font-size: 20px; font-weight: 600; color: var(--text); }
  .panel-sub { margin: 4px 0 0; font-size: 13px; color: var(--text-5); }

  .loading { display: flex; align-items: center; gap: 10px; color: var(--text-5); font-size: 13px; padding: 32px 0; }
  .spinner {
    width: 18px; height: 18px; border: 2px solid var(--border-2); border-top-color: var(--accent);
    border-radius: 50%; animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .empty {
    display: flex; flex-direction: column; align-items: center;
    gap: 10px; padding: 60px 0; text-align: center;
  }
  .empty-icon { font-size: 36px; color: var(--border-3); margin-bottom: 4px; }
  .empty p { margin: 0; font-size: 14px; color: var(--text-4); }
  .empty-sub { font-size: 12px; color: var(--text-6) !important; max-width: 380px; }

  .filter-wrap {
    margin-bottom: 16px;
    max-width: 320px;
  }

  .ext-list { display: flex; flex-direction: column; gap: 12px; }

  .ext-card {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    transition: border-color 0.15s;
  }
  .ext-card:hover { border-color: var(--border-2); }

  .ext-card-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding: 14px 16px 10px;
    gap: 12px;
  }

  .ext-info { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
  .ext-name { font-size: 14px; font-weight: 600; color: var(--text-2); }
  .ext-desc { font-size: 12px; color: var(--text-5); }
  .ext-date { font-size: 11px; color: var(--text-7); }

  .ext-actions { display: flex; gap: 6px; flex-shrink: 0; }
  .edit-btn {
    background: var(--accent-dim); border: 1px solid var(--accent-border); color: var(--accent);
    border-radius: 6px; padding: 5px 12px; font-size: 12px; cursor: pointer;
  }
  .edit-btn:hover { background: var(--accent-dim); border-color: var(--accent); }
  .del-btn {
    background: transparent; border: 1px solid var(--danger-border); color: var(--text-5);
    border-radius: 6px; padding: 5px 10px; font-size: 12px; cursor: pointer;
  }
  .del-btn:hover:not(:disabled) { background: var(--danger-bg); color: var(--danger-text); border-color: var(--danger-border); }
  .del-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  .code-preview {
    margin: 0;
    padding: 10px 16px 14px;
    background: var(--bg);
    border-top: 1px solid var(--border);
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px;
    color: var(--text-5);
    line-height: 1.6;
    overflow: hidden;
    white-space: pre-wrap;
    word-break: break-all;
  }

  .overlay {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.8);
    display: flex; align-items: center; justify-content: center;
    z-index: 400;
  }
  .editor-modal {
    background: var(--bg-3);
    border: 1px solid var(--border-2);
    border-radius: 12px;
    width: 740px;
    max-width: 96vw;
    max-height: 92vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .editor-head {
    display: flex; justify-content: space-between; align-items: center;
    padding: 18px 22px 14px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  h3 { margin: 0; font-size: 15px; font-weight: 600; color: var(--text); }
  .close-btn { background: none; border: none; color: var(--text-5); cursor: pointer; font-size: 15px; }

  .editor-body {
    padding: 18px 22px 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    overflow-y: auto;
  }

  label {
    display: flex; flex-direction: column; gap: 5px;
    font-size: 12px; color: var(--text-3);
  }
  label input {
    background: var(--bg-2); border: 1px solid var(--border-2); border-radius: 6px;
    padding: 7px 10px; color: var(--text); font-size: 13px; font-family: inherit;
  }
  label input:focus { outline: none; border-color: var(--accent); }

  .code-label-row { display: flex; align-items: center; gap: 8px; }
  .code-label { font-size: 12px; color: var(--text-3); }
  .hint { font-size: 11px; color: var(--text-5); }
  .hint code { font-size: 10px; background: var(--accent-dim); padding: 1px 4px; border-radius: 3px; color: var(--accent); }

  .code-editor {
    background: var(--bg);
    border: 1px solid var(--border-2);
    border-radius: 6px;
    padding: 12px 14px;
    color: var(--text-2);
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 12px;
    line-height: 1.65;
    resize: vertical;
    min-height: 280px;
    tab-size: 2;
  }
  .code-editor:focus { outline: none; border-color: var(--accent); }

  .editor-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px; }

  .btn-primary {
    background: var(--accent); color: #fff; border: none;
    border-radius: 7px; padding: 8px 18px; font-size: 13px; font-weight: 600; cursor: pointer;
  }
  .btn-primary:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary {
    background: transparent; color: var(--text-4); border: 1px solid var(--border-2);
    border-radius: 7px; padding: 8px 18px; font-size: 13px; cursor: pointer;
  }
  .btn-secondary:hover { border-color: var(--text-5); color: var(--text-2); }

  .err { color: var(--danger-text); font-size: 12px; margin: 0; }
</style>
