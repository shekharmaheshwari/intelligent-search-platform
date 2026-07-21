/**
 * analyticsController — owns all side-effectful analytics actions.
 *
 * Layer contract:
 *   AnalyticsPanel (UI events)
 *     → analyticsController (business logic)
 *       → analyticsStore (state)
 *         → AnalyticsRepository (HTTP)
 *
 * Components must NOT call analyticsStore.load() / setRange() directly.
 * They call these methods and read reactive state from the store.
 */
import { analyticsStore } from '../stores/analytics-store';
import type { AnalyticsState } from '../types';

export const analyticsController = {
  /** Called once when the Analytics panel mounts with the first available index. */
  init(indexId: string): void {
    analyticsStore.load(indexId);
  },

  /** Called when the user picks a different index from the dropdown. */
  changeIndex(indexId: string): void {
    if (!indexId) return;
    analyticsStore.load(indexId);
  },

  /** Called when the user clicks a range tab (7d / 30d / 90d). */
  changeRange(range: AnalyticsState['range']): void {
    // setRange updates state and triggers a reload using the current indexId.
    analyticsStore.setRange(range);
  },

  /** Called when the Analytics panel unmounts (navigate away). */
  teardown(): void {
    analyticsStore.reset();
  },
};
