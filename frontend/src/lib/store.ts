/**
 * Barrel re-export of all Svelte stores.
 *
 * Importing from this file gives access to all reactive stores.
 * Each store is a singleton that wraps StoreBase<T> with domain action methods.
 */
export { appStateStore } from './stores/app-state-store';
export { indexesStore } from './stores/indexes-store';
export { extensionsStore } from './stores/extensions-store';
export { settingsStore } from './stores/settings-store';
