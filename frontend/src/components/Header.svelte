<script lang="ts">
  /**
   * Header — top navigation bar.
   *
   * Contains the ISP logo/home button and a Docs link.
   * Clicking the logo resets the app to the dashboard view with no selected index.
   */
  import { appState } from '../lib/store';

  /** Navigate to the dashboard view and deselect any active index. */
  function goHome() {
    appState.update(s => ({ ...s, view: 'dashboard', selectedIndexId: null }));
  }
</script>

<header>
  <button class="logo" on:click={goHome}>
    <span class="icon">⬡</span>
    <span class="name">Intelligent Search Platform</span>
  </button>
  <nav>
    <a href="https://github.com" target="_blank" rel="noopener">Docs</a>
  </nav>
</header>

<style>
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 24px;
    height: 56px;
    background: #0f0f0f;
    border-bottom: 1px solid #222;
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
    color: #fff;
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.2px;
  }
  .icon {
    font-size: 22px;
    color: #7c6cf4;
  }
  nav a {
    color: #888;
    text-decoration: none;
    font-size: 13px;
  }
  nav a:hover { color: #fff; }
</style>
