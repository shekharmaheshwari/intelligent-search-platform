<script lang="ts">
  export let value = '';
  export let ariaLabel = 'Filter';
</script>

<div class="filter-bar">
  <span class="icon">⌕</span>
  <input
    type="text"
    placeholder="Filter…"
    bind:value
    aria-label={ariaLabel}
  />
  {#if value}
    <button class="clear" on:click={() => value = ''} aria-label="Clear filter">✕</button>
  {/if}
</div>

<style>
  .filter-bar {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--bg-3);
    border: 1px solid var(--border-2);
    border-radius: 6px;
    padding: 0 10px;
    height: 32px;
  }
  .filter-bar:focus-within {
    border-color: var(--accent-border);
  }
  .icon {
    font-size: 14px;
    color: var(--text-6);
    flex-shrink: 0;
    line-height: 1;
  }
  input {
    flex: 1;
    background: none;
    border: none;
    outline: none;
    color: var(--text-2);
    font-size: 12px;
    font-family: inherit;
    min-width: 0;
  }
  input::placeholder { color: var(--text-6); }
  .clear {
    background: none;
    border: none;
    color: var(--text-6);
    font-size: 10px;
    cursor: pointer;
    padding: 0;
    line-height: 1;
    flex-shrink: 0;
  }
  .clear:hover { color: var(--text-3); }
</style>
