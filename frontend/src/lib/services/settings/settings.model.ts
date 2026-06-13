import type { EmbedModelOption } from '../../types';

export interface Settings {
  embedModel: string;
  embedModelNumDim: number;
  availableModels: EmbedModelOption[];
  dimChanged?: boolean;
  openaiApiKeySet?: boolean;
  rexbertEndpoint?: string | null;
  rexbertApiKeySet?: boolean;
  customEndpoint?: string | null;
  customModelName?: string | null;
  customNumDim?: number | null;
}
