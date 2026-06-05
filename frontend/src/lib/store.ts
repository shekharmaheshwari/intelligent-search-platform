/**
 * Global Svelte stores shared across all components.
 *
 * - ``appState`` — current navigation state (which view is active, which index is selected).
 * - ``indexes``  — the full list of ISP sources; refreshed on mount and after discover/create/delete.
 * - ``loading``  — true while the initial index list is being fetched.
 * - ``error``    — holds the last global error string; rendered as a toast in App.svelte.
 */
import { writable } from 'svelte/store';
import type { AppState, IndexMeta } from './types';

/** Tracks which view is visible and which index is selected. */
export const appState = writable<AppState>({
  view: 'dashboard',
  selectedIndexId: null,
  detailTab: 'overview',
});

/** Complete list of ISP indexes, kept in sync with the backend on every mutation. */
export const indexes = writable<IndexMeta[]>([]);

/** True while the initial GET /v1/indexes call is in flight. */
export const loading = writable(false);

/** Last uncaught error message; displayed as a fixed toast at the bottom-right. */
export const error = writable<string | null>(null);
