// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import type { AppSettings, EmbedModelOption } from '../../lib/types';

/**
 * Return the ``EmbedModelOption`` for ``id`` from the loaded settings, or ``null`` when not found.
 * Used to display dimension badges and provider colours in the model picker step.
 */
export function embedModelOption(settings: AppSettings, id: string): EmbedModelOption | null {
  return settings.available_models.find(m => m.id === id) ?? null;
}

/**
 * Return ``true`` when switching to ``selectedEmbedModel`` would change the vector dimension.
 * Shows a pre-save warning in the embedding model step so the user knows existing
 * indexes must be rebuilt before semantic search will work correctly.
 */
export function embedDimWillChange(settings: AppSettings, selectedEmbedModel: string): boolean {
  const cur  = embedModelOption(settings, settings.embed_model);
  const next = embedModelOption(settings, selectedEmbedModel);
  return !!cur && !!next && cur.num_dim > 0 && next.num_dim > 0 && cur.num_dim !== next.num_dim;
}
