<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../lib/api';
  import type { ExtensionMeta } from '../lib/types';

  let extensions: ExtensionMeta[] = [];
  let loading = false;
  let errMsg = '';

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
    loading = true; errMsg = '';
    try { extensions = await api.listExtensions(); }
    catch (e) { errMsg = String(e); }
    finally { loading = false; }
  }

  onMount(load);

  function openCreate() {
    editingId = ''; editName = ''; editDesc = ''; editCode = DEFAULT_CODE;
    editorErr = ''; showEditor = true;
  }

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
        await api.updateExtension(editingId, { name: editName.trim(), description: editDesc.trim(), code: editCode });
      } else {
        await api.createExtension(editName.trim(), editDesc.trim(), editCode);
      }
      showEditor = false;
      await load();
    } catch (e) {
      editorErr = String(e);
    } finally {
      saving = false;
    }
  }

  let deletingId = '';
  async function deleteExt(id: string) {
    if (!confirm('Delete this extension? Indexes using it will lose the assignment.')) return;
    deletingId = id;
    try { await api.deleteExtension(id); await load(); }
    catch (e) { errMsg = String(e); }
    finally { deletingId = ''; }
  }

  function fmtDate(s: string) {
    return new Date(s).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
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
    <div class="ext-list">
      {#each extensions as ext}
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
  h2 { margin: 0; font-size: 20px; font-weight: 600; color: #eee; }
  .panel-sub { margin: 4px 0 0; font-size: 13px; color: #555; }

  .loading { display: flex; align-items: center; gap: 10px; color: #555; font-size: 13px; padding: 32px 0; }
  .spinner {
    width: 18px; height: 18px; border: 2px solid #222; border-top-color: #7c6cf4;
    border-radius: 50%; animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .empty {
    display: flex; flex-direction: column; align-items: center;
    gap: 10px; padding: 60px 0; text-align: center;
  }
  .empty-icon { font-size: 36px; color: #2a2a2a; margin-bottom: 4px; }
  .empty p { margin: 0; font-size: 14px; color: #666; }
  .empty-sub { font-size: 12px; color: #444 !important; max-width: 380px; }

  .ext-list { display: flex; flex-direction: column; gap: 12px; }

  .ext-card {
    background: #0f0f0f;
    border: 1px solid #1e1e1e;
    border-radius: 10px;
    overflow: hidden;
    transition: border-color 0.15s;
  }
  .ext-card:hover { border-color: #2e2e2e; }

  .ext-card-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding: 14px 16px 10px;
    gap: 12px;
  }

  .ext-info { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
  .ext-name { font-size: 14px; font-weight: 600; color: #ddd; }
  .ext-desc { font-size: 12px; color: #555; }
  .ext-date { font-size: 11px; color: #3a3a3a; }

  .ext-actions { display: flex; gap: 6px; flex-shrink: 0; }
  .edit-btn {
    background: #1a1a2a; border: 1px solid #2e2b55; color: #9d91f5;
    border-radius: 6px; padding: 5px 12px; font-size: 12px; cursor: pointer;
  }
  .edit-btn:hover { background: #2a2552; }
  .del-btn {
    background: transparent; border: 1px solid #2a1a1a; color: #664;
    border-radius: 6px; padding: 5px 10px; font-size: 12px; cursor: pointer;
  }
  .del-btn:hover:not(:disabled) { background: #2a0f0f; color: #e55; border-color: #5a1a1a; }
  .del-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  .code-preview {
    margin: 0;
    padding: 10px 16px 14px;
    background: #070707;
    border-top: 1px solid #161616;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px;
    color: #555;
    line-height: 1.6;
    overflow: hidden;
    white-space: pre-wrap;
    word-break: break-all;
  }

  /* Editor overlay */
  .overlay {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.8);
    display: flex; align-items: center; justify-content: center;
    z-index: 400;
  }
  .editor-modal {
    background: #111;
    border: 1px solid #222;
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
    border-bottom: 1px solid #1a1a1a;
    flex-shrink: 0;
  }
  h3 { margin: 0; font-size: 15px; font-weight: 600; color: #eee; }
  .close-btn { background: none; border: none; color: #555; cursor: pointer; font-size: 15px; }

  .editor-body {
    padding: 18px 22px 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    overflow-y: auto;
  }

  label {
    display: flex; flex-direction: column; gap: 5px;
    font-size: 12px; color: #888;
  }
  label input {
    background: #151515; border: 1px solid #2e2e2e; border-radius: 6px;
    padding: 7px 10px; color: #eee; font-size: 13px; font-family: inherit;
  }
  label input:focus { outline: none; border-color: #7c6cf4; }

  .code-label-row { display: flex; align-items: center; gap: 8px; }
  .code-label { font-size: 12px; color: #888; }
  .hint { font-size: 11px; color: #555; }
  .hint code { font-size: 10px; background: #1e1e1e; padding: 1px 4px; border-radius: 3px; color: #9d91f5; }

  .code-editor {
    background: #080808;
    border: 1px solid #2e2e2e;
    border-radius: 6px;
    padding: 12px 14px;
    color: #c9d1d9;
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 12px;
    line-height: 1.65;
    resize: vertical;
    min-height: 280px;
    tab-size: 2;
  }
  .code-editor:focus { outline: none; border-color: #7c6cf4; }

  .editor-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px; }

  .btn-primary {
    background: #7c6cf4; color: #fff; border: none;
    border-radius: 7px; padding: 8px 18px; font-size: 13px; font-weight: 600; cursor: pointer;
  }
  .btn-primary:hover:not(:disabled) { background: #6b5ce7; }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary {
    background: transparent; color: #888; border: 1px solid #333;
    border-radius: 7px; padding: 8px 18px; font-size: 13px; cursor: pointer;
  }
  .btn-secondary:hover { border-color: #555; color: #aaa; }

  .err { color: #e55; font-size: 12px; margin: 0; }
</style>
