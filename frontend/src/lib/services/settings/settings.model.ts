// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

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
  customerContextEnabled?: boolean;
  ccVectorBlendingEnabled?: boolean;
  ccAffinityBoostEnabled?: boolean;
  ccViewedPenaltyEnabled?: boolean;
}
