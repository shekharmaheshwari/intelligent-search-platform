import type { AppSettings } from '../../types';
import type { Settings } from './settings.model';

/**
 * Map the ``AppSettings`` DTO to the ``Settings`` domain model.
 * Converts snake_case API fields to camelCase.
 */
export function mapAppSettingsToSettings(dto: AppSettings): Settings {
  return {
    embedModel: dto.embed_model,
    embedModelNumDim: dto.embed_model_num_dim,
    availableModels: dto.available_models,
    dimChanged: dto.dim_changed,
    openaiApiKeySet: dto.openai_api_key_set,
    rexbertEndpoint: dto.rexbert_endpoint,
    rexbertApiKeySet: dto.rexbert_api_key_set,
    customEndpoint: dto.custom_endpoint,
    customModelName: dto.custom_model_name,
    customNumDim: dto.custom_num_dim,
  };
}
