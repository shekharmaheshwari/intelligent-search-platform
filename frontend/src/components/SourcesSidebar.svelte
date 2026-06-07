<script lang="ts">
  /**
   * SourcesSidebar — left navigation panel showing all indexed sources.
   *
   * Dispatches:
   * - ``select``     — user clicked a source row (detail: index id)
   * - ``create``     — user clicked the "+" button to open the Create Wizard
   * - ``delete``     — user clicked the ✕ delete button (detail: index id)
   * - ``extensions`` — user clicked the "⚙ Extensions" footer link
   * - ``settings``   — user clicked the "⚙ Settings" footer link
   */
  import { createEventDispatcher } from 'svelte';
  import type { IndexMeta } from '../lib/types';

  export let indexes: IndexMeta[] = [];
  export let selectedId: string | null = null;

  const dispatch = createEventDispatcher<{ select: string; create: void; delete: string; extensions: void; settings: void }>();

  let sourcesOpen = true;

  /**
   * Return the primary connector URL for display in the sidebar.
   * For external sources this is the Typesense collection name (no real URL).
   */
  function connectorUrl(idx: IndexMeta): string {
    if (idx.is_external) return idx.external_collection ?? '';
    if (!idx.connectors.length) return '';
    return String(idx.connectors[0].url ?? '');
  }

  /** Return the connector type badge string (``rest``, ``sitemap``, ``typesense``, or ``''``). */
  function connectorType(idx: IndexMeta): string {
    if (idx.is_external) return 'typesense';
    if (!idx.connectors.length) return '';
    return String(idx.connectors[0].type ?? '');
  }

  /** Shorten a URL to hostname + truncated path for compact sidebar display. */
  function shortUrl(url: string): string {
    try {
      const u = new URL(url);
      return u.hostname + u.pathname.slice(0, 28) + (u.pathname.length > 28 ? '…' : '');
    } catch {
      return url.slice(0, 36);
    }
  }
</script>

<aside class="sidebar">

  <!-- Sources group -->
  <div class="group">
    <div class="group-header">
      <button
        class="group-toggle"
        on:click={() => sourcesOpen = !sourcesOpen}
        aria-expanded={sourcesOpen}
      >
        <span class="chevron" class:open={sourcesOpen}>›</span>
        <span class="group-label">Sources</span>
        <span class="group-count">{indexes.length}</span>
      </button>
      <button
        class="add-btn"
        on:click|stopPropagation={() => dispatch('create')}
        title="Add source"
        aria-label="Add source"
      >+</button>
    </div>

    {#if sourcesOpen}
      {#if indexes.length === 0}
        <div class="empty">
          <p>No sources yet.</p>
          <button class="add-link" on:click={() => dispatch('create')}>Add your first source →</button>
        </div>
      {:else}
        <ul class="source-list">
          {#each indexes as idx}
            <li
              class="source-item"
              class:active={selectedId === idx.id}
              on:click={() => dispatch('select', idx.id)}
              on:keydown={e => e.key === 'Enter' && dispatch('select', idx.id)}
              role="button"
              tabindex="0"
            >
              <div class="source-row">
                <div class="source-info">
                  <span class="source-name">{idx.name}</span>
                  {#if idx.description}
                    <span class="source-desc">{idx.description}</span>
                  {/if}
                  {#if connectorUrl(idx)}
                    <span class="source-url">{shortUrl(connectorUrl(idx))}</span>
                  {/if}
                </div>
                <div class="source-meta">
                  {#if connectorType(idx)}
                    <span class="type-badge">{connectorType(idx)}</span>
                  {/if}
                  <span class="doc-count">{idx.doc_count.toLocaleString()}</span>
                  <button
                    class="del-btn"
                    on:click|stopPropagation={() => dispatch('delete', idx.id)}
                    aria-label="Delete source"
                  >✕</button>
                </div>
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    {/if}
  </div>

  <div class="sidebar-footer">
    <button class="footer-link" on:click={() => dispatch('extensions')}>
      <span class="footer-icon">⚙</span> Extensions
    </button>
    <button class="footer-link" on:click={() => dispatch('settings')}>
      <span class="footer-icon">⚒</span> Settings
    </button>
  </div>
</aside>

<style>
  .sidebar {
    width: 256px;
    min-width: 256px;
    background: #0d0d0d;
    border-right: 1px solid #1a1a1a;
    display: flex;
    flex-direction: column;
    height: calc(100vh - 56px);
    position: sticky;
    top: 56px;
    overflow-y: auto;
  }

  .sidebar-footer {
    margin-top: auto;
    border-top: 1px solid #161616;
    padding: 8px 10px;
  }
  .footer-link {
    display: flex;
    align-items: center;
    gap: 7px;
    width: 100%;
    background: none;
    border: none;
    color: #444;
    font-size: 12px;
    cursor: pointer;
    padding: 7px 10px;
    border-radius: 6px;
    text-align: left;
    transition: background 0.1s, color 0.1s;
  }
  .footer-link:hover { background: #131313; color: #888; }
  .footer-icon { font-size: 13px; }

  @media (max-width: 767px) {
    .sidebar {
      width: 100%;
      min-width: 0;
      height: calc(100vh - 56px);
      border-right: none;
    }
  }

  /* ── Group ── */
  .group {
    display: flex;
    flex-direction: column;
  }

  .group-header {
    display: flex;
    align-items: center;
    padding: 10px 10px 10px 12px;
    gap: 0;
    position: relative;
  }
  .group-header:hover .add-btn { opacity: 1; }

  .group-toggle {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 6px;
    background: none;
    border: none;
    cursor: pointer;
    padding: 0;
    min-width: 0;
  }

  .chevron {
    font-size: 14px;
    color: #444;
    line-height: 1;
    transition: transform 0.15s;
    display: inline-block;
    transform: rotate(0deg);
  }
  .chevron.open { transform: rotate(90deg); }

  .group-label {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.7px;
    color: #555;
  }
  .group-toggle:hover .group-label { color: #888; }

  .group-count {
    font-size: 10px;
    color: #3a3a3a;
    background: #181818;
    border-radius: 8px;
    padding: 1px 5px;
    margin-left: 2px;
  }

  .add-btn {
    background: none;
    border: none;
    color: #555;
    width: 20px;
    height: 20px;
    border-radius: 4px;
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 0.15s, background 0.1s, color 0.1s;
    flex-shrink: 0;
  }
  .group-header:hover .add-btn { opacity: 1; }
  .add-btn:hover { background: #1e1b3a; color: #9d91f5; }

  /* ── Empty ── */
  .empty {
    padding: 10px 16px 14px 28px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .empty p { margin: 0; font-size: 12px; color: #3a3a3a; }
  .add-link {
    background: none;
    border: none;
    color: #7c6cf4;
    font-size: 12px;
    cursor: pointer;
    padding: 0;
    text-align: left;
  }
  .add-link:hover { text-decoration: underline; }

  /* ── Source list ── */
  .source-list {
    list-style: none;
    margin: 0;
    padding: 2px 0 6px;
  }

  .source-item {
    padding: 8px 10px 8px 22px;
    cursor: pointer;
    border-left: 2px solid transparent;
    transition: background 0.1s, border-color 0.1s;
    position: relative;
  }
  .source-item:hover { background: #131313; }
  .source-item.active {
    background: #131313;
    border-left-color: #7c6cf4;
  }

  .source-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 6px;
  }

  .source-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
    flex: 1;
  }

  .source-name {
    font-size: 13px;
    font-weight: 500;
    color: #ccc;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.3;
  }
  .source-item.active .source-name { color: #eee; }

  .source-desc {
    font-size: 11px;
    color: #4a4a4a;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .source-url {
    font-size: 10px;
    color: #383838;
    font-family: 'SF Mono', 'Fira Code', monospace;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .source-meta {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 3px;
    flex-shrink: 0;
  }

  .type-badge {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    color: #6b5ce7;
    background: #16142a;
    padding: 1px 4px;
    border-radius: 3px;
    letter-spacing: 0.3px;
  }

  .doc-count {
    font-size: 10px;
    color: #3a3a3a;
  }

  .del-btn {
    background: none;
    border: none;
    color: #444;
    cursor: pointer;
    font-size: 12px;
    padding: 2px 4px;
    line-height: 1;
    border-radius: 4px;
    transition: background 0.1s, color 0.1s;
    flex-shrink: 0;
  }
  .del-btn:hover { background: #2a0f0f; color: #e55; }
</style>
