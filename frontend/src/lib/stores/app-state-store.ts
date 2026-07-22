import { StoreBase } from './StoreBase';
import type { AppState, View } from '../types';

/**
 * Manages global navigation state — which view is visible and which index is selected.
 *
 * All routing decisions go through this store so the URL-less SPA has a single
 * source of truth for the current page.
 */
class AppStateStore extends StoreBase<AppState> {
  constructor() {
    super({ view: 'dashboard', selectedIndexId: null, detailTab: 'overview' });
  }

  /**
   * Navigate to the detail view for an index.
   * Resets the detail tab to ``overview`` so stale tab state is never shown.
   */
  selectIndex(id: string): void {
    this.update(s => ({ ...s, selectedIndexId: id, view: 'detail', detailTab: 'overview' }));
  }

  /**
   * Navigate to any named view, optionally changing the selected index.
   * When ``selectedIndexId`` is ``undefined`` the current selection is preserved.
   */
  navigateTo(view: View, selectedIndexId?: string | null): void {
    this.update(s => ({
      ...s,
      view,
      ...(selectedIndexId !== undefined ? { selectedIndexId } : {}),
    }));
  }

  /**
   * Open the configure/connector-settings view for an index.
   * Resets the detail tab to ``overview``.
   */
  configure(id: string): void {
    this.update(s => ({ ...s, selectedIndexId: id, view: 'configure', detailTab: 'overview' }));
  }

  /** Navigate back from the configure view to the detail content view. */
  backToDetail(): void {
    this.update(s => ({ ...s, view: 'detail' }));
  }

  /** Navigate to the analytics panel. */
  navigateToAnalytics(): void {
    this.update(s => ({ ...s, view: 'analytics', selectedIndexId: null }));
  }

  /** Switch the active tab within the detail view without changing the selected index. */
  setDetailTab(tab: AppState['detailTab']): void {
    this.update(s => ({ ...s, detailTab: tab }));
  }
}

export const appStateStore = new AppStateStore();
