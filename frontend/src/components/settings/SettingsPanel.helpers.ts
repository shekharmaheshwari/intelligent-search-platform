import type { AppSettings, EmbedModelOption } from '../../lib/types';

/**
 * Return the ``EmbedModelOption`` for ``id`` from the settings catalogue, or ``null`` when not found.
 * Used to display the dimension badge and description card for the selected model.
 */
export function modelOption(settings: AppSettings, id: string): EmbedModelOption | null {
  return settings.available_models.find(m => m.id === id) ?? null;
}

/**
 * Return the effective vector dimension for the model identified by ``id``.
 * When ``id`` is ``'custom'`` the dimension is whatever the user has typed in
 * ``customNumDim`` rather than a catalogue value.
 */
export function effectiveDim(settings: AppSettings, id: string, customNumDim: number): number {
  if (id === 'custom') return customNumDim || 0;
  return modelOption(settings, id)?.num_dim ?? 0;
}

/**
 * Return ``true`` when switching to ``selectedModel`` would change the vector dimension.
 * Used to show a pre-save warning so the user knows existing indexes must be rebuilt.
 */
export function dimensionWillChange(settings: AppSettings, selectedModel: string, customNumDim: number): boolean {
  const currentDim = settings.embed_model === 'custom'
    ? (settings.custom_num_dim ?? 0)
    : (modelOption(settings, settings.embed_model)?.num_dim ?? 0);
  const nextDim = effectiveDim(settings, selectedModel, customNumDim);
  return nextDim !== currentDim && nextDim > 0;
}
