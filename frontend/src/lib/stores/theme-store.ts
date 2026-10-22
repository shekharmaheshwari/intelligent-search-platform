// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { writable } from 'svelte/store';

function createThemeStore() {
  const initial: 'light' | 'dark' =
    typeof localStorage !== 'undefined' && localStorage.getItem('isp-theme') === 'light'
      ? 'light'
      : 'dark';
  const { subscribe, set } = writable<'light' | 'dark'>(initial);
  return {
    subscribe,
    toggle() {
      const next: 'light' | 'dark' =
        document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('isp-theme', next);
      set(next);
    },
  };
}

export const themeStore = createThemeStore();
