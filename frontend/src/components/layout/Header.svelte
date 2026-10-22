<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  import { appStateStore, themeStore } from '../../lib/store';

  function goHome() {
    appStateStore.navigateTo('dashboard', null);
  }
</script>

<header>
  <button class="logo" on:click={goHome}>
    <span class="icon">⬡</span>
    <span class="name">Intelligent Search Platform</span>
  </button>
  <nav>
    <button class="theme-btn" on:click={themeStore.toggle} title="Toggle theme">
      {$themeStore === 'light' ? '☾' : '☀'}
    </button>
    <a href="/docs/index.html" target="_blank" rel="noopener">Docs</a>
  </nav>
</header>

<style>
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 24px;
    height: 56px;
    background: var(--bg-2);
    border-bottom: 1px solid var(--border-2);
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .logo {
    display: flex;
    align-items: center;
    gap: 10px;
    background: none;
    border: none;
    cursor: pointer;
    color: var(--text);
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.2px;
  }
  .icon {
    font-size: 22px;
    color: var(--accent);
  }
  nav {
    display: flex;
    align-items: center;
    gap: 16px;
  }
  nav a {
    color: var(--text-4);
    text-decoration: none;
    font-size: 13px;
  }
  nav a:hover { color: var(--text); }
  .theme-btn {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 16px;
    color: var(--text-4);
    padding: 2px 4px;
    line-height: 1;
  }
  .theme-btn:hover { color: var(--text); }
</style>
