/** A JS transform extension from the global library (mirrors backend extensions table). */
export interface ExtensionMeta {
  id: string;
  name: string;
  description: string;
  code: string;
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
