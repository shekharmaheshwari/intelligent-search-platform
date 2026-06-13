/**
 * Format an ISO 8601 date string to a short human-readable locale date.
 * Example output: ``"Jun 5, 2026"``.
 */
export function fmtDate(s: string): string {
  return new Date(s).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}
