// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import type { ExtensionMeta } from '../../lib/types/extension.types';

/**
 * Format an ISO 8601 date string to a short human-readable locale date.
 * Example output: ``"Jun 5, 2026"``.
 */
export function fmtDate(s: string): string {
  return new Date(s).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

/**
 * Filter extensions by a search query, matching against name and description.
 * Returns the original array unchanged when query is empty.
 */
export function filterExtensions(extensions: ExtensionMeta[], query: string): ExtensionMeta[] {
  const q = query.trim().toLowerCase();
  if (!q) return extensions;
  return extensions.filter(ext =>
    ext.name.toLowerCase().includes(q) ||
    (ext.description ?? '').toLowerCase().includes(q)
  );
}
