/**
 * One entry in the model catalogue returned by GET /v1/settings.
 * ``num_dim`` is the vector dimension the model produces (0 for the "custom"
 * sentinel whose dimension is user-supplied). ``provider`` is one of
 * ``"voyage"``, ``"openai"``, or ``"custom"``.
 */
export interface EmbedModelOption {
  id: string;
  label: string;
  description: string;
  num_dim: number;
  provider: string;
}

/**
 * Full settings payload returned by GET /v1/settings and PUT /v1/settings.
 *
 * ``dim_changed`` is only present on the PUT response; it is ``true`` when the
 * newly selected model produces vectors of a different dimension than the previous
 * model, meaning existing indexes need a full rebuild.
 *
 * Custom-provider fields (``custom_endpoint``, ``custom_model_name``,
 * ``custom_num_dim``) are populated when the active model is "custom".
 * ``openai_api_key_set`` is ``true`` when an OpenAI key is stored — the key
 * itself is never returned by the API.
 */
export interface AppSettings {
  embed_model: string;
  embed_model_num_dim: number;
  available_models: EmbedModelOption[];
  dim_changed?: boolean;
  openai_api_key_set?: boolean;
  rexbert_endpoint?: string | null;
  rexbert_api_key_set?: boolean;
  custom_endpoint?: string | null;
  custom_model_name?: string | null;
  custom_num_dim?: number | null;
}
