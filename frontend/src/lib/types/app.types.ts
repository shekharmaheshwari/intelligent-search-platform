/** Which top-level panel is currently visible in App.svelte. */
export type View = 'dashboard' | 'detail' | 'create' | 'configure' | 'extensions' | 'settings';

/** Global UI state held in the ``appStateStore`` Svelte store. */
export interface AppState {
  view: View;
  selectedIndexId: string | null;
  detailTab: 'overview' | 'connector' | 'mapping' | 'documents' | 'search' | 'api' | 'suggestions' | 'personalisation';
}
