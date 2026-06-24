<script lang="ts">
  /**
   * LoginGate — wraps the entire app.
   *
   * When PUBLIC_AUTH_ENABLED !== 'true' this component is transparent — it just
   * renders the slot.  When auth is on it:
   *   1. Reads ?isp_token= from the URL after the OAuth callback and stores it.
   *   2. Shows a login screen if no valid token is present.
   *   3. Renders the slot (the app) when the user is authenticated.
   */
  import { onMount } from 'svelte';
  import { authStore } from '../../lib/stores/auth-store';

  const AUTH_ENABLED = import.meta.env.PUBLIC_AUTH_ENABLED === 'true';
  const API_BASE = import.meta.env.PUBLIC_API_URL?.replace('/v1', '') || 'http://localhost:8001';

  let checked = false;

  onMount(() => {
    if (!AUTH_ENABLED) { checked = true; return; }

    // Pick up token from OAuth callback redirect (?isp_token=...)
    const params = new URLSearchParams(window.location.search);
    const token = params.get('isp_token');
    if (token) {
      authStore.setToken(token);
      params.delete('isp_token');
      const clean = window.location.pathname + (params.toString() ? '?' + params : '');
      window.history.replaceState({}, '', clean);
    }

    checked = true;
  });

  $: user = $authStore;

  function signIn() {
    window.location.href = `${API_BASE}/auth/login`;
  }

  function signOut() {
    fetch(`${API_BASE}/auth/logout`, { method: 'POST' }).finally(() => {
      authStore.clear();
    });
  }
</script>

{#if !AUTH_ENABLED || !checked}
  <slot />
{:else if user}
  <slot />
  <!-- Thin sign-out bar rendered at the top-right by Header via CSS; kept here for simplicity -->
{:else}
  <div class="login-screen">
    <div class="login-card">
      <div class="logo">⬡</div>
      <h1>Intelligent Search Platform</h1>
      <p>Sign in to access the admin interface.</p>
      <button class="sign-in-btn" on:click={signIn}>
        Sign in with {import.meta.env.PUBLIC_OAUTH_PROVIDER === 'github' ? 'GitHub' : 'Google'}
      </button>
    </div>
  </div>
{/if}

<style>
  .login-screen {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--bg);
  }
  .login-card {
    background: var(--bg-2);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 48px 40px;
    width: 360px;
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
  }
  .logo {
    font-size: 40px;
    color: var(--accent);
  }
  h1 {
    font-size: 20px;
    font-weight: 700;
    color: var(--text);
    margin: 0;
  }
  p {
    font-size: 13px;
    color: var(--text-4);
    margin: 0;
  }
  .sign-in-btn {
    margin-top: 8px;
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 10px 24px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.15s;
    width: 100%;
  }
  .sign-in-btn:hover {
    opacity: 0.88;
  }
</style>
