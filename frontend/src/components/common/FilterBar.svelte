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
    background: #111;
    border: 1px solid #222;
    border-radius: 6px;
    padding: 0 10px;
    height: 32px;
  }
  .filter-bar:focus-within {
    border-color: #3a3370;
  }
  .icon {
    font-size: 14px;
    color: #444;
    flex-shrink: 0;
    line-height: 1;
  }
  input {
    flex: 1;
    background: none;
    border: none;
    outline: none;
    color: #ccc;
    font-size: 12px;
    font-family: inherit;
    min-width: 0;
  }
  input::placeholder { color: #3a3a3a; }
  .clear {
    background: none;
    border: none;
    color: #444;
    font-size: 10px;
    cursor: pointer;
    padding: 0;
    line-height: 1;
    flex-shrink: 0;
  }
  .clear:hover { color: #888; }
</style>
