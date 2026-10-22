// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { settingsRepository, type SettingsRepository } from '../../repository/SettingsRepository';
import { mapAppSettingsToSettings } from './settings.mapper';
import type { Settings } from './settings.model';

/**
 * Service for application settings operations.
 *
 * Calls the repository, applies the mapper, and returns a domain ``Settings`` model.
 * No direct HTTP — all network I/O goes through ``SettingsRepository``.
 */
export class SettingsService {
  constructor(private readonly repo: SettingsRepository) {}

  /**
   * Fetch current settings, including the active embedding model and the
   * full catalogue of available models.
   */
  async getSettings(): Promise<Settings> {
    const dto = await this.repo.getSettings();
    return mapAppSettingsToSettings(dto);
  }

  /**
   * Update the active embedding model and any associated provider config.
   *
   * The change is applied at runtime immediately — no container restart required.
   * The returned ``Settings.dimChanged`` flag is ``true`` when the new model
   * produces vectors of a different dimension, signalling that all indexes must
   * be rebuilt before semantic search will work.
   *
   * @param modelId         One of the ``id`` values from ``Settings.availableModels``.
   * @param opts.openaiApiKey    Required when an OpenAI model is selected for the first time.
   * @param opts.customEndpoint  Full URL of a custom OpenAI-compatible endpoint.
   * @param opts.customNumDim    Vector dimension returned by the custom endpoint.
   */
  async updateSettings(
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
  ): Promise<Settings> {
    const dto = await this.repo.updateSettings(modelId, opts);
    return mapAppSettingsToSettings(dto);
  }

  /**
   * Save RexBERT endpoint and optional API key without changing the global model.
   *
   * Call this when the global model is voyage/openai but one or more indexes use
   * a per-index RexBERT override — the endpoint must be stored so the backend can
   * reach the RexBERT server at ingest and search time.
   */
  async saveRexbertConfig(endpoint: string, apiKey?: string): Promise<Settings> {
    const dto = await this.repo.saveRexbertConfig(endpoint, apiKey);
    return mapAppSettingsToSettings(dto);
  }

  /**
   * Update one or more customer-context personalisation flags.
   * Only the supplied fields are changed — partial updates are safe.
   */
  async updateCustomerContext(flags: {
    enabled?: boolean;
    vectorBlendingEnabled?: boolean;
    affinityBoostEnabled?: boolean;
    viewedPenaltyEnabled?: boolean;
  }): Promise<Settings> {
    const dto = await this.repo.updateCustomerContext(flags);
    return mapAppSettingsToSettings(dto);
  }
}

export const settingsService = new SettingsService(settingsRepository);
