/**
 * Format an ISO 8601 date string to a short human-readable locale date.
 * Example output: ``"Jun 5, 2026"``.
 */
export function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}
