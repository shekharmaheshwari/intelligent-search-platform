// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import type { EmbedModelOption } from '../../lib/types';

/**
 * Format an ISO 8601 date string to a short human-readable locale date.
 * Example output: ``"Jun 5, 2026"``.
 */
export function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

/**
 * Return the vector dimension for a given model id.
 * Returns 0 when the model id is empty (global default) or not found in the list.
 */
export function dimForModel(modelId: string, models: EmbedModelOption[]): number {
  if (!modelId) return 0;
  return models.find(m => m.id === modelId)?.num_dim ?? 0;
}

/**
 * Return true when switching from ``currentModelId`` to ``nextModelId`` would
 * produce vectors of a different dimension, requiring a full index rebuild.
 * Returns false when either dimension is unknown (0).
 */
export function embedDimWillChange(
  currentModelId: string,
  nextModelId: string,
  models: EmbedModelOption[],
): boolean {
  const cur  = dimForModel(currentModelId, models);
  const next = dimForModel(nextModelId, models);
  return cur > 0 && next > 0 && cur !== next;
}
