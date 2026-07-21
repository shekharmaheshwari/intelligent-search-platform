<script lang="ts">
  import AgentSetup   from './AgentSetup.svelte';
  import ShopifySetup from './ShopifySetup.svelte';

  let activeTab: 'setup' | 'shopify' = 'setup';
</script>

<div class="panel">
  <div class="panel-head">
    <div>
      <h2>Agent</h2>
      <p class="panel-sub">AI-powered tools for building and managing your search indexes.</p>
    </div>
  </div>

  <nav class="tab-bar">
    <button
      class="tab-btn"
      class:tab-active={activeTab === 'setup'}
      on:click={() => activeTab = 'setup'}
    >
      Setup Content
    </button>
    <button
      class="tab-btn"
      class:tab-active={activeTab === 'shopify'}
      on:click={() => activeTab = 'shopify'}
    >
      Shopify
    </button>
  </nav>

  {#if activeTab === 'setup'}
    <div class="tab-content">
      <AgentSetup />
    </div>
  {:else if activeTab === 'shopify'}
    <div class="tab-content">
      <ShopifySetup />
    </div>
  {/if}
</div>

<style>
  .panel {
    padding: 28px 32px;
    max-width: 860px;
  }

  .panel-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    margin-bottom: 4px;
  }

  .panel-head h2 {
    font-size: 18px;
    font-weight: 600;
    color: var(--text-1);
    margin: 0 0 4px;
  }

  .panel-sub {
    font-size: 13px;
    color: var(--text-5);
    margin: 0;
  }

  .tab-bar {
    display: flex;
    border-bottom: 1px solid var(--border);
    margin: 20px 0 0;
  }

  .tab-btn {
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 10px 18px;
    font-size: 13px;
    font-weight: 500;
    color: var(--text-5);
    cursor: pointer;
    margin-bottom: -1px;
    transition: color 0.15s, border-color 0.15s;
    font-family: inherit;
  }

  .tab-btn:hover { color: var(--text-2); }

  .tab-active {
    color: var(--accent);
    border-bottom-color: var(--accent);
    font-weight: 600;
  }

  .tab-content {
    padding-top: 24px;
  }
</style>
