import { writable } from 'svelte/store';
import type { AppState, IndexMeta } from './types';

export const appState = writable<AppState>({
  view: 'dashboard',
  selectedIndexId: null,
  detailTab: 'overview',
});

export const indexes = writable<IndexMeta[]>([]);
export const loading = writable(false);
export const error = writable<string | null>(null);
