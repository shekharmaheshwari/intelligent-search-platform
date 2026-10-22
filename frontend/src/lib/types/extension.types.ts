// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

/** A JS transform extension from the global library (mirrors backend extensions table). */
export interface ExtensionMeta {
  id: string;
  name: string;
  description: string;
  code: string;
  /** ``'transform'`` runs per-item via ``transform(doc)``; ``'pre_processor'`` runs once on the full raw response via ``pre_process(response)``. */
  type: 'transform' | 'pre_processor';
  created_at: string;
  updated_at: string;
}

/**
 * An extension assigned to a specific index.
 * Extends ``ExtensionMeta`` with ``run_order`` (execution sequence, lower runs first)
 * and ``enabled`` (can be toggled off without removing the assignment).
 */
export interface IndexExtension extends ExtensionMeta {
  run_order: number;
  enabled: boolean;
}
