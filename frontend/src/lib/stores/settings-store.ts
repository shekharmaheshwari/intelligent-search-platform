// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { StoreBase } from './StoreBase';
import { settingsService } from '../services/settings/SettingsService';
import type { AppSettings } from '../types';

interface SettingsState {
  /** Loaded settings; ``null`` until the first successful load. */
  settings: AppSettings | null;
  /** True while a load or save is in flight. */
  loading: boolean;
  /** Last error message; ``null`` when the last operation succeeded. */
  error: string | null;
}

/**
 * Store for application-wide settings (embedding model, provider config).
 *
 * Holds the settings as raw ``AppSettings`` DTO so components can bind directly
 * to the same types used in the UI without an extra mapping step.
 */
class SettingsStore extends StoreBase<SettingsState> {
  constructor() {
    super({ settings: null, loading: false, error: null });
  }

  /**
   * Fetch current settings from the backend and replace ``settings`` in the store.
   * Sets ``loading`` while the request is in flight and clears it on completion.
   */
  async load(): Promise<void> {
    this.update(s => ({ ...s, loading: true, error: null }));
    try {
      const domain = await settingsService.getSettings();
      const dto: AppSettings = {
        embed_model: domain.embedModel,
        embed_model_num_dim: domain.embedModelNumDim,
        available_models: domain.availableModels,
        dim_changed: domain.dimChanged,
        openai_api_key_set: domain.openaiApiKeySet,
        rexbert_endpoint: domain.rexbertEndpoint,
        rexbert_api_key_set: domain.rexbertApiKeySet,
        custom_endpoint: domain.customEndpoint,
        custom_model_name: domain.customModelName,
        custom_num_dim: domain.customNumDim,
        customer_context_enabled:   domain.customerContextEnabled,
        cc_vector_blending_enabled: domain.ccVectorBlendingEnabled,
        cc_affinity_boost_enabled:  domain.ccAffinityBoostEnabled,
        cc_viewed_penalty_enabled:  domain.ccViewedPenaltyEnabled,
      };
      this.update(s => ({ ...s, settings: dto, loading: false }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }

  /**
   * Persist the selected model and any provider-specific fields.
   * Updates ``settings`` in the store with the response from the backend.
   *
   * @returns The updated ``AppSettings`` DTO so callers can inspect ``dim_changed``.
   */
  async save(
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
    const domain = await settingsService.updateSettings(modelId, opts);
    const dto: AppSettings = {
      embed_model: domain.embedModel,
      embed_model_num_dim: domain.embedModelNumDim,
      available_models: domain.availableModels,
      dim_changed: domain.dimChanged,
      openai_api_key_set: domain.openaiApiKeySet,
      rexbert_endpoint: domain.rexbertEndpoint,
      rexbert_api_key_set: domain.rexbertApiKeySet,
      custom_endpoint: domain.customEndpoint,
      custom_model_name: domain.customModelName,
      custom_num_dim: domain.customNumDim,
      customer_context_enabled:   domain.customerContextEnabled,
      cc_vector_blending_enabled: domain.ccVectorBlendingEnabled,
      cc_affinity_boost_enabled:  domain.ccAffinityBoostEnabled,
      cc_viewed_penalty_enabled:  domain.ccViewedPenaltyEnabled,
    };
    this.update(s => ({ ...s, settings: dto }));
    return dto;
  }

  /**
   * Update one or more customer-context personalisation flags.
   * Merges the response from the backend into the store's settings DTO.
   */
  async updateCustomerContext(flags: {
    enabled?: boolean;
    vectorBlendingEnabled?: boolean;
    affinityBoostEnabled?: boolean;
    viewedPenaltyEnabled?: boolean;
  }): Promise<void> {
    this.update(s => ({ ...s, loading: true, error: null }));
    try {
      const domain = await settingsService.updateCustomerContext(flags);
      this.update(s => ({
        ...s,
        settings: s.settings ? {
          ...s.settings,
          customer_context_enabled:   domain.customerContextEnabled,
          cc_vector_blending_enabled: domain.ccVectorBlendingEnabled,
          cc_affinity_boost_enabled:  domain.ccAffinityBoostEnabled,
          cc_viewed_penalty_enabled:  domain.ccViewedPenaltyEnabled,
        } : s.settings,
        loading: false,
      }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }
}

export const settingsStore = new SettingsStore();
