import { writable, get } from 'svelte/store';

export interface AuthUser {
  email: string;
  name: string;
  token: string;
  exp: number;
}

const STORAGE_KEY = 'isp-auth-token';

function parseJwt(token: string): { sub: string; name: string; exp: number } | null {
  try {
    const payload = token.split('.')[1];
    return JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));
  } catch {
    return null;
  }
}

function createAuthStore() {
  const { subscribe, set } = writable<AuthUser | null>(null);

  function _load() {
    if (typeof localStorage === 'undefined') return;
    const token = localStorage.getItem(STORAGE_KEY);
    if (!token) return;
    const payload = parseJwt(token);
    if (!payload || payload.exp < Date.now() / 1000) {
      localStorage.removeItem(STORAGE_KEY);
      return;
    }
    set({ token, email: payload.sub, name: payload.name ?? '', exp: payload.exp });
  }

  _load();

  return {
    subscribe,

    /** Store a JWT (called after OAuth callback). */
    setToken(token: string) {
      const payload = parseJwt(token);
      if (!payload || payload.exp < Date.now() / 1000) return;
      localStorage.setItem(STORAGE_KEY, token);
      set({ token, email: payload.sub, name: payload.name ?? '', exp: payload.exp });
    },

    clear() {
      localStorage.removeItem(STORAGE_KEY);
      set(null);
    },

    /** Return the raw JWT string if valid, null otherwise. */
    getToken(): string | null {
      const u = get({ subscribe });
      if (!u || u.exp < Date.now() / 1000) return null;
      return u.token;
    },
  };
}

export const authStore = createAuthStore();
