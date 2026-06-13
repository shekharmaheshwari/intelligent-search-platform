/**
 * Format a Unix timestamp (seconds) to a short locale date string.
 * Example output: ``"6/5/2026"`` (locale-dependent).
 */
export function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString();
}
