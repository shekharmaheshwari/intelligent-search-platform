<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  /**
   * App — root layout and routing component.
   *
   * Owns the overall page structure (Header + Sidebar + Main) and all
   * cross-cutting concerns:
   * - Index list loading and auto-discovery on mount.
   * - Delete confirmation modal.
   * - Navigation state via the ``appState`` store.
   * - Mobile sidebar visibility toggle.
   *
   * Child components communicate upward via Svelte ``createEventDispatcher``
   * events; this component translates them into store mutations or API calls.
   */
  import { onMount, onDestroy } from 'svelte';
  import { appStateStore, indexesStore } from '../../lib/store';
  import { stateToSearch, searchToState, isTabSwitch } from './App.helpers';
  import Header from './Header.svelte';
  import SourcesSidebar from '../sources/SourcesSidebar.svelte';
  import SourceContent from '../sources/SourceContent.svelte';
  import IndexDetail from '../sources/IndexDetail.svelte';
  import CreateWizard from '../sources/CreateWizard.svelte';
  import ExtensionsPanel from '../extensions/ExtensionsPanel.svelte';
  import SettingsPanel from '../settings/SettingsPanel.svelte';
  import AgentPanel from '../settings/AgentPanel.svelte';
  import DocsHome from './DocsHome.svelte';
  import AnalyticsPanel from '../analytics/AnalyticsPanel.svelte';

  let showWizard = false;
  let windowWidth = 1024;
  $: isMobile = windowWidth < 768;
  let sidebarVisible = true;

  $: state = $appStateStore;
  $: idxState = $indexesStore;

  // ── Delete confirmation ──────────────────────────────────────────────────────
  let pendingDeleteId: string | null = null;
  let deleteDeleting = false;
  $: pendingDeleteName = idxState.indexes.find(i => i.id === pendingDeleteId)?.name ?? '';

  function confirmDelete(id: string) {
    pendingDeleteId = id;
  }

  async function executeDelete() {
    if (!pendingDeleteId) return;
    deleteDeleting = true;
    try {
      await indexesStore.deleteIndex(pendingDeleteId);
      if (state.selectedIndexId === pendingDeleteId) {
        appStateStore.navigateTo('dashboard', null);
      }
    } finally {
      pendingDeleteId = null;
      deleteDeleting = false;
    }
  }

  async function loadIndexes() {
    await indexesStore.load();
    if (!state.selectedIndexId && idxState.indexes.length > 0) {
      appStateStore.selectIndex(idxState.indexes[0].id);
    }
  }

  async function initLoad() {
    await indexesStore.load();
    if (!state.selectedIndexId && idxState.indexes.length > 0) {
      appStateStore.selectIndex(idxState.indexes[0].id);
    }
    try {
      const discovered = await indexesStore.discover();
      if (discovered.length > 0) {
        await indexesStore.load();
      }
    } catch { /* ignore discovery errors */ }
  }

  onMount(async () => {
    window.addEventListener('popstate', onPopstate);
    const fromUrl = searchToState(window.location.search);
    await initLoad();
    // URL-derived state overrides the auto-selected first index
    if (fromUrl.view !== 'dashboard' || fromUrl.selectedIndexId) {
      applyUrlState(fromUrl);
    }
    _prevSearch = stateToSearch(appStateStore.getValue());
    _urlSyncEnabled = true;
  });

  onDestroy(() => {
    if (typeof window !== 'undefined') window.removeEventListener('popstate', onPopstate);
  });

  function selectSource(id: string) {
    appStateStore.selectIndex(id);
    if (isMobile) sidebarVisible = false;
  }

  function configure(id: string) {
    appStateStore.configure(id);
  }

  function backToContent() {
    appStateStore.backToDetail();
  }

  function deleteSource(id: string) {
    confirmDelete(id);
  }

  function onWizardDone(id: string) {
    showWizard = false;
    selectSource(id);
    loadIndexes();
  }

  $: selectedIndex = idxState.indexes.find(i => i.id === state.selectedIndexId) ?? null;

  // ── URL sync ─────────────────────────────────────────────────────────────────

  let _urlSyncEnabled = false;
  let _prevSearch = '';

  // Runs only after onMount sets _urlSyncEnabled = true, so window/history are available.
  $: if (_urlSyncEnabled) {
    const next = stateToSearch(state);
    if (next !== _prevSearch) {
      if (isTabSwitch(_prevSearch, state)) {
        history.replaceState(null, '', window.location.pathname + next);
      } else {
        history.pushState(null, '', window.location.pathname + next);
      }
      _prevSearch = next;
    }
  }

  function applyUrlState(partial: ReturnType<typeof searchToState>): void {
    if (partial.view === 'configure' && partial.selectedIndexId) {
      appStateStore.configure(partial.selectedIndexId);
      if (partial.detailTab && partial.detailTab !== 'overview') {
        appStateStore.setDetailTab(partial.detailTab);
      }
      if (isMobile) sidebarVisible = false;
    } else if (partial.view === 'analytics') {
      appStateStore.navigateToAnalytics();
      if (isMobile) sidebarVisible = false;
    } else if (partial.view === 'extensions') {
      appStateStore.navigateTo('extensions');
      if (isMobile) sidebarVisible = false;
    } else if (partial.view === 'settings') {
      appStateStore.navigateTo('settings');
      if (isMobile) sidebarVisible = false;
    } else if (partial.selectedIndexId) {
      appStateStore.selectIndex(partial.selectedIndexId);
      if (isMobile) sidebarVisible = false;
    } else {
      appStateStore.navigateTo('dashboard', null);
    }
  }

  function onPopstate(): void {
    applyUrlState(searchToState(window.location.search));
    _prevSearch = stateToSearch(appStateStore.getValue());
  }
</script>

<svelte:window bind:innerWidth={windowWidth} />

<div class="app">
  <Header />

  <div class="body">
    <div class="sidebar-wrap" class:mobile-hidden={isMobile && !sidebarVisible}>
      <SourcesSidebar
        indexes={idxState.indexes}
        selectedId={state.selectedIndexId}
        on:select={e => selectSource(e.detail)}
        on:create={() => showWizard = true}
        on:delete={e => deleteSource(e.detail)}
        on:agent={() => appStateStore.navigateTo('agent', null)}
        on:analytics={() => appStateStore.navigateToAnalytics()}
        on:extensions={() => appStateStore.navigateTo('extensions', null)}
        on:settings={() => appStateStore.navigateTo('settings', null)}
      />
    </div>

    <div class="main" class:mobile-hidden={isMobile && sidebarVisible}>
      {#if isMobile}
        <button class="back-btn" on:click={() => sidebarVisible = true}>← Sources</button>
      {/if}
      {#if state.view === 'agent'}
        <div class="extensions-wrap">
          <AgentPanel />
        </div>
      {:else if state.view === 'analytics'}
        <div class="extensions-wrap">
          <AnalyticsPanel indexes={idxState.indexes} />
        </div>
      {:else if state.view === 'settings'}
        <div class="extensions-wrap">
          <SettingsPanel />
        </div>
      {:else if state.view === 'extensions'}
        <div class="extensions-wrap">
          <ExtensionsPanel />
        </div>
      {:else if state.view === 'configure' && state.selectedIndexId}
        <div class="configure-wrap">
          <div class="configure-back">
            <button on:click={backToContent}>← Back to content</button>
          </div>
          <IndexDetail
            indexId={state.selectedIndexId}
            tab={state.detailTab}
            on:back={backToContent}
            on:tabchange={e => appStateStore.setDetailTab(e.detail)}
          />
        </div>
      {:else if selectedIndex}
        <SourceContent
          index={selectedIndex}
          on:configure={e => configure(e.detail)}
        />
      {:else if idxState.indexes.length === 0 && !idxState.loading}
        <div class="welcome">
          <div class="welcome-icon">⬡</div>
          <h2>Welcome to Intelligent Search Platform</h2>
          <p>Add a source to start indexing your content and making it searchable.</p>
          <button class="btn-primary" on:click={() => showWizard = true}>Add your first source</button>
        </div>
      {:else if state.view === 'dashboard'}
        <!-- Indexes exist and loading is done but no index is selected (e.g.
             user clicked the logo). Show the documentation home instead of a
             meaningless spinner. -->
        <DocsHome />
      {:else}
        <!-- True loading state: still fetching indexes for the first time. -->
        <div class="welcome">
          <div class="spinner"></div>
        </div>
      {/if}

      {#if idxState.error}
        <div class="global-error">{idxState.error}</div>
      {/if}
    </div>
  </div>

  {#if isMobile && sidebarVisible}
    <button class="fab-add" on:click={() => showWizard = true} aria-label="Add source">+</button>
  {/if}

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
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }

  .body {
    display: flex;
    height: calc(100vh - 56px);
    overflow: hidden;
  }

  .sidebar-wrap {
    display: flex;
    flex-shrink: 0;
  }

  .main {
    flex: 1;
    min-width: 0;
    overflow-y: auto;
  }

  .mobile-hidden {
    display: none !important;
  }

  .back-btn {
    display: none;
    background: none;
    border: none;
    color: var(--accent);
    font-size: 13px;
    cursor: pointer;
    padding: 12px 20px 4px;
    text-align: left;
  }
  .back-btn:hover { text-decoration: underline; }

  .fab-add {
    display: none;
  }

  @media (max-width: 767px) {
    .sidebar-wrap {
      width: 100%;
    }

    .back-btn {
      display: block;
    }

    .configure-wrap {
      padding: 16px;
    }

    .configure-back {
      display: none;
    }

    .fab-add {
      display: flex;
      position: fixed;
      bottom: 24px;
      right: 24px;
      width: 48px;
      height: 48px;
      border-radius: 50%;
      background: var(--accent);
      color: #fff;
      border: none;
      font-size: 24px;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      box-shadow: 0 4px 16px rgba(124, 108, 244, 0.4);
      z-index: 50;
    }
  }

  .extensions-wrap {
    height: 100%;
    overflow-y: auto;
  }

  .configure-wrap {
    height: 100%;
    overflow-y: auto;
    padding: 28px 32px;
    overflow-x: hidden;
  }

  .configure-back {
    margin-bottom: 16px;
  }
  .configure-back button {
    background: none;
    border: none;
    color: var(--accent);
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
  .welcome-icon { font-size: 48px; color: var(--border-3); }
  .welcome h2 { margin: 0; font-size: 20px; color: var(--text-5); font-weight: 500; }
  .welcome p { margin: 0; font-size: 13px; color: var(--text-6); max-width: 360px; }

  .btn-primary {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    margin-top: 8px;
  }
  .btn-primary:hover { background: var(--accent-hover); }

  .spinner {
    width: 32px; height: 32px;
    border: 3px solid var(--surface);
    border-top-color: var(--accent);
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
    background: var(--bg-3);
    border: 1px solid var(--border-2);
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
  .confirm-icon { font-size: 28px; color: var(--danger-text); }
  .confirm-title { margin: 0; font-size: 16px; font-weight: 600; color: var(--text); }
  .confirm-body { margin: 0; font-size: 13px; color: var(--text-4); line-height: 1.55; }
  .confirm-body strong { color: var(--text-2); }
  .confirm-actions {
    display: flex;
    gap: 10px;
    margin-top: 6px;
    width: 100%;
    justify-content: flex-end;
  }
  .btn-cancel-modal {
    background: transparent;
    border: 1px solid var(--border-3);
    color: var(--text-4);
    border-radius: 7px;
    padding: 8px 18px;
    font-size: 13px;
    cursor: pointer;
  }
  .btn-cancel-modal:hover { border-color: var(--text-6); color: var(--text-3); }
  .btn-delete-modal {
    background: var(--danger-bg);
    border: 1px solid var(--danger-border);
    color: var(--danger-text);
    border-radius: 7px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .btn-delete-modal:hover:not(:disabled) { opacity: 0.85; }
  .btn-delete-modal:disabled, .btn-cancel-modal:disabled { opacity: 0.5; cursor: not-allowed; }

  .global-error {
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: var(--danger-bg);
    border: 1px solid var(--danger-border);
    color: var(--danger-text);
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 13px;
    max-width: 360px;
  }
</style>
