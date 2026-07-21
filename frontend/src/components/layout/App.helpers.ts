import type { AppState, View } from '../../lib/types';

/** Encode AppState as URL search params string (e.g. "?index=abc&view=configure&tab=boost-rules"). */
export function stateToSearch(s: AppState): string {
  const p = new URLSearchParams();
  if (s.selectedIndexId) p.set('index', s.selectedIndexId);
  if (s.view === 'configure') {
    p.set('view', 'configure');
    if (s.detailTab && s.detailTab !== 'overview') p.set('tab', s.detailTab);
  } else if (s.view === 'extensions' || s.view === 'settings' || s.view === 'agent') {
    p.set('view', s.view);
  }
  // 'detail' is encoded as just ?index=<id>
  // 'dashboard' produces no params → bare /
  const qs = p.toString();
  return qs ? `?${qs}` : '';
}

/** Parse URL search params string back into partial AppState. */
export function searchToState(search: string): Partial<AppState> {
  const p       = new URLSearchParams(search);
  const indexId = p.get('index');
  const view    = p.get('view') as View | null;
  const tab     = p.get('tab') as AppState['detailTab'] | null;

  if (view === 'configure' && indexId) {
    return { view: 'configure', selectedIndexId: indexId, detailTab: tab ?? 'overview' };
  }
  if (view === 'agent')      return { view: 'agent',      selectedIndexId: null };
  if (view === 'extensions') return { view: 'extensions', selectedIndexId: null };
  if (view === 'settings')   return { view: 'settings',   selectedIndexId: null };
  if (indexId)               return { view: 'detail',     selectedIndexId: indexId, detailTab: 'overview' };
  return { view: 'dashboard', selectedIndexId: null, detailTab: 'overview' };
}

/** True when the navigation is only a tab change within the same configure view. */
export function isTabSwitch(prevSearch: string, next: AppState): boolean {
  return next.view === 'configure'
    && prevSearch.includes(`index=${next.selectedIndexId}`)
    && prevSearch.includes('view=configure');
}
