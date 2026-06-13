import type { AppSettings } from '../types';
import { api } from '../api';

/**
 * Repository for application settings HTTP calls.
 *
 * Delegates directly to the ``api`` client; returns raw DTOs unchanged.
 */
export class SettingsRepository {
  /**
   * Fetch current application settings including the active embedding model
   * and the full catalogue of available models.
   */
  getSettings(): Promise<AppSettings> {
    return api.getSettings();
  }

  /**
   * Update the active embedding model and any associated provider config.
   *
   * The change is applied immediately at runtime — no container restart required.
   *
   * @param modelId         One of the ``id`` values from ``AppSettings.available_models``.
   * @param opts.openaiApiKey    Required the first time an OpenAI model is selected.
   * @param opts.customEndpoint  Full URL of a custom OpenAI-compatible endpoint.
   * @param opts.customNumDim    Vector dimension returned by the custom endpoint.
   */
  updateSettings(
    modelId: string,
    opts?: {
      openaiApiKey?: string;
      rexbertEndpoint?: string;
      rexbertApiKey?: string;
      customEndpoint?: string;
      customApiKey?: string;
      customModelName?: string;
      customNumDim?: number;
    }
  ): Promise<AppSettings> {
    return api.updateSettings(modelId, opts);
  }
}

export const settingsRepository = new SettingsRepository();
