<script lang="ts">
  import { onMount } from 'svelte';
  import { appState, indexes, loading, error } from '../lib/store';
  import { api } from '../lib/api';
  import Header from './Header.svelte';
  import SourcesSidebar from './SourcesSidebar.svelte';
  import SourceContent from './SourceContent.svelte';
  import IndexDetail from './IndexDetail.svelte';
  import CreateWizard from './CreateWizard.svelte';

  let showWizard = false;

  // ── Delete confirmation ──────────────────────────────────────────────────────
  let pendingDeleteId: string | null = null;
  let deleteDeleting = false;
  $: pendingDeleteName = $indexes.find(i => i.id === pendingDeleteId)?.name ?? '';

  function confirmDelete(id: string) {
    pendingDeleteId = id;
  }

  async function executeDelete() {
    if (!pendingDeleteId) return;
    deleteDeleting = true;
    try {
      await api.deleteIndex(pendingDeleteId);
      if ($appState.selectedIndexId === pendingDeleteId) {
        appState.update(s => ({ ...s, selectedIndexId: null, view: 'dashboard' }));
      }
      await loadIndexes();
    } finally {
      pendingDeleteId = null;
      deleteDeleting = false;
    }
  }

  async function loadIndexes() {
    loading.set(true);
    error.set(null);
    try {
      const list = await api.listIndexes();
      indexes.set(list);
      if (!$appState.selectedIndexId && list.length > 0) {
        appState.update(s => ({ ...s, selectedIndexId: list[0].id, view: 'detail' }));
      }
    } catch (e) {
      error.set(String(e));
    } finally {
      loading.set(false);
    }
  }

  async function initLoad() {
    // First load existing indexes, then discover external collections (idempotent)
    await loadIndexes();
    try {
      const discovered = await api.discoverIndexes();
      if (discovered.length > 0) {
        // Reload to include newly discovered sources
        await loadIndexes();
      }
    } catch { /* ignore discovery errors */ }
  }

  onMount(initLoad);

  function selectSource(id: string) {
    appState.update(s => ({ ...s, selectedIndexId: id, view: 'detail', detailTab: 'overview' }));
  }

  function configure(id: string) {
    appState.update(s => ({ ...s, selectedIndexId: id, view: 'configure', detailTab: 'overview' }));
  }

  function backToContent() {
    appState.update(s => ({ ...s, view: 'detail' }));
  }

  function deleteSource(id: string) {
    confirmDelete(id);
  }

  function onWizardDone(id: string) {
    showWizard = false;
    selectSource(id);
    loadIndexes();
  }

  $: selectedIndex = $indexes.find(i => i.id === $appState.selectedIndexId) ?? null;
</script>

<div class="app">
  <Header />

  <div class="body">
    <SourcesSidebar
      indexes={$indexes}
      selectedId={$appState.selectedIndexId}
      on:select={e => selectSource(e.detail)}
      on:create={() => showWizard = true}
      on:delete={e => deleteSource(e.detail)}
    />

    <div class="main">
      {#if $appState.view === 'configure' && $appState.selectedIndexId}
        <div class="configure-wrap">
          <div class="configure-back">
            <button on:click={backToContent}>← Back to content</button>
          </div>
          <IndexDetail
            indexId={$appState.selectedIndexId}
            tab={$appState.detailTab}
            on:back={backToContent}
          />
        </div>
      {:else if selectedIndex}
        <SourceContent
          index={selectedIndex}
          on:configure={e => configure(e.detail)}
        />
      {:else if $indexes.length === 0 && !$loading}
        <div class="welcome">
          <div class="welcome-icon">⬡</div>
          <h2>Welcome to Intelligent Search Platform</h2>
          <p>Add a source to start indexing your content and making it searchable.</p>
          <button class="btn-primary" on:click={() => showWizard = true}>Add your first source</button>
        </div>
      {:else}
        <div class="welcome">
          <div class="spinner"></div>
        </div>
      {/if}

      {#if $error}
        <div class="global-error">{$error}</div>
      {/if}
    </div>
  </div>

  {#if showWizard}
    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
    <div class="overlay" on:click|self={() => showWizard = false} role="dialog" aria-modal="true">
      <CreateWizard
        on:done={e => onWizardDone(e.detail)}
        on:cancel={() => showWizard = false}
      />
    </div>
  {/if}

  {#if pendingDeleteId}
    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
    <div class="overlay" on:click|self={() => pendingDeleteId = null} role="dialog" aria-modal="true">
      <div class="confirm-modal">
        <div class="confirm-icon">⚠</div>
        <h3 class="confirm-title">Remove source</h3>
        <p class="confirm-body">
          Remove <strong>{pendingDeleteName}</strong>?<br>
          All indexed content will be deleted from Typesense.
        </p>
        <div class="confirm-actions">
          <button class="btn-cancel-modal" on:click={() => pendingDeleteId = null} disabled={deleteDeleting}>
            Cancel
          </button>
          <button class="btn-delete-modal" on:click={executeDelete} disabled={deleteDeleting}>
            {deleteDeleting ? 'Removing…' : 'Yes, remove'}
          </button>
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .app {
    min-height: 100vh;
    background: #0a0a0a;
    color: #eee;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }

  .body {
    display: flex;
    height: calc(100vh - 56px);
    overflow: hidden;
  }

  .main {
    flex: 1;
    min-width: 0;
    overflow-y: auto;
  }

  .configure-wrap {
    height: 100%;
    overflow-y: auto;
    padding: 28px 32px;
  }

  .configure-back {
    margin-bottom: 16px;
  }
  .configure-back button {
    background: none;
    border: none;
    color: #7c6cf4;
    cursor: pointer;
    font-size: 13px;
    padding: 0;
  }
  .configure-back button:hover { text-decoration: underline; }

  .welcome {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    gap: 14px;
    text-align: center;
    padding: 40px;
  }
  .welcome-icon { font-size: 48px; color: #2a2a2a; }
  .welcome h2 { margin: 0; font-size: 20px; color: #555; font-weight: 500; }
  .welcome p { margin: 0; font-size: 13px; color: #444; max-width: 360px; }

  .btn-primary {
    background: #7c6cf4;
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    margin-top: 8px;
  }
  .btn-primary:hover { background: #6b5ce7; }

  .spinner {
    width: 32px; height: 32px;
    border: 3px solid #1a1a1a;
    border-top-color: #7c6cf4;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.75);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 200;
  }

  .confirm-modal {
    background: #111;
    border: 1px solid #222;
    border-radius: 12px;
    padding: 28px 28px 22px;
    width: 360px;
    max-width: 94vw;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    text-align: center;
  }
  .confirm-icon { font-size: 28px; color: #c44; }
  .confirm-title { margin: 0; font-size: 16px; font-weight: 600; color: #eee; }
  .confirm-body { margin: 0; font-size: 13px; color: #777; line-height: 1.55; }
  .confirm-body strong { color: #ccc; }
  .confirm-actions {
    display: flex;
    gap: 10px;
    margin-top: 6px;
    width: 100%;
    justify-content: flex-end;
  }
  .btn-cancel-modal {
    background: transparent;
    border: 1px solid #2a2a2a;
    color: #777;
    border-radius: 7px;
    padding: 8px 18px;
    font-size: 13px;
    cursor: pointer;
  }
  .btn-cancel-modal:hover { border-color: #444; color: #aaa; }
  .btn-delete-modal {
    background: #7a1c1c;
    border: 1px solid #a02020;
    color: #ffaaaa;
    border-radius: 7px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .btn-delete-modal:hover:not(:disabled) { background: #8f2020; }
  .btn-delete-modal:disabled, .btn-cancel-modal:disabled { opacity: 0.5; cursor: not-allowed; }

  .global-error {
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: #1a0f0f;
    border: 1px solid #3a1e1e;
    color: #e55;
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 13px;
    max-width: 360px;
  }
</style>
