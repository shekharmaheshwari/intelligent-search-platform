import type { IndexMeta } from '../../lib/types';

/**
 * Return the primary connector URL for sidebar display.
 * For external (catalog write-back) sources returns the Typesense collection name
 * because there is no real fetch URL on those indexes.
 */
export function connectorUrl(idx: IndexMeta): string {
  if (idx.is_external) return idx.external_collection ?? '';
  if (!idx.connectors.length) return '';
  return String(idx.connectors[0].url ?? '');
}

/**
 * Return the connector type badge string shown next to each source row.
 * External sources always show ``typesense``; managed sources show ``rest`` or ``sitemap``.
 * Returns ``''`` when no connector is configured.
 */
export function connectorType(idx: IndexMeta): string {
  if (idx.is_external) return 'typesense';
  if (!idx.connectors.length) return '';
  return String(idx.connectors[0].type ?? '');
}

/**
 * Filter indexes by a search query, matching against name, description, and connector URL.
 * Returns the original array unchanged when query is empty.
 */
export function filterIndexes(indexes: IndexMeta[], query: string): IndexMeta[] {
  const q = query.trim().toLowerCase();
  if (!q) return indexes;
  return indexes.filter(idx =>
    idx.name.toLowerCase().includes(q) ||
    (idx.description ?? '').toLowerCase().includes(q) ||
    connectorUrl(idx).toLowerCase().includes(q)
  );
}

/**
 * Shorten ``url`` to ``hostname + truncated path`` for compact sidebar display.
 * Falls back to the raw first 36 characters when the value is not a valid URL.
 */
export function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname + u.pathname.slice(0, 28) + (u.pathname.length > 28 ? '…' : '');
  } catch {
    return url.slice(0, 36);
  }
}
