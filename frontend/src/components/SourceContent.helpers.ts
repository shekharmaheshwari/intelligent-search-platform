import type { IndexMeta, DocumentItem } from '../lib/types';

/**
 * Truncate ``text`` to ``max`` characters, appending an ellipsis when cut.
 * Used for document content previews in the document list.
 */
export function excerpt(text: string, max = 180): string {
  return text.length > max ? text.slice(0, max) + '…' : text;
}

/**
 * Format a Unix timestamp (seconds) to a short human-readable locale date.
 * Example output: ``"Jun 5, 2026"``.
 */
export function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

/**
 * Return the URL string of the first connector on a managed index.
 * Returns an empty string when no connectors are configured.
 */
export function connectorUrl(idx: IndexMeta): string {
  if (!idx.connectors.length) return '';
  return String(idx.connectors[0].url ?? '');
}

/**
 * Return all key-value pairs of a document for the expanded field table.
 * Preserves insertion order so fields appear in the same order as the API response.
 */
export function docFields(doc: DocumentItem): [string, unknown][] {
  return Object.entries(doc);
}

/**
 * Attempt to parse ``v`` as a JSON object.
 * Returns the parsed object when ``v`` is a string starting with ``{``, or ``null`` on failure.
 */
export function tryParseJson(v: unknown): Record<string, unknown> | null {
  if (!v || typeof v !== 'string') return null;
  const s = v.trim();
  if (!s.startsWith('{')) return null;
  try { return JSON.parse(s); } catch { return null; }
}

/**
 * Return ``true`` for the special ``metadata`` field, which is rendered
 * as a parsed sub-table instead of a plain string value.
 */
export function isMetadataKey(key: string): boolean {
  return key === 'metadata';
}

/**
 * Stringify any document field value for plain text display.
 * Strings are returned unchanged; objects are pretty-printed with two-space indent.
 */
export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  return JSON.stringify(v, null, 2);
}

/**
 * Format a parsed metadata sub-field value for the metadata sub-table.
 * Booleans → ``Yes``/``No``, arrays → comma-separated list,
 * objects → JSON string, anything else → ``String(v)``.
 * Returns ``'—'`` for null/undefined.
 */
export function formatMetaValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (Array.isArray(v)) return v.join(', ') || '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

/**
 * Extract the popularity score from a document.
 * Checks the top-level ``popularity`` field first; falls back to the parsed
 * ``metadata`` blob for catalog write-back documents that store it there.
 */
export function getPopularity(doc: DocumentItem): number {
  if (typeof doc.popularity === 'number') return doc.popularity;
  const meta = tryParseJson(doc.metadata as string | null ?? null);
  return typeof meta?.popularity === 'number' ? meta.popularity : 0;
}

/**
 * Extract the ``is_featured`` flag from a document.
 * Checks the top-level field first; falls back to the parsed ``metadata`` blob.
 */
export function getIsFeatured(doc: DocumentItem): boolean {
  if (typeof doc.is_featured === 'boolean') return doc.is_featured;
  const meta = tryParseJson(doc.metadata as string | null ?? null);
  return Boolean(meta?.is_featured);
}
