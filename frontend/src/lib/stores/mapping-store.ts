// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { StoreBase } from './StoreBase';
import { indexService } from '../services/indexes/IndexService';
import type { MappingConfig } from '../types';

interface MappingSavingState {
  label:   boolean;
  mapping: boolean;
}

interface MappingErrorState {
  label:   string;
  mapping: string;
}

interface MappingState {
  saving: MappingSavingState;
  errors: MappingErrorState;
}

class MappingStore extends StoreBase<MappingState> {
  constructor() {
    super({ saving: { label: false, mapping: false }, errors: { label: '', mapping: '' } });
  }

  async saveIntentLabel(indexId: string, label: string): Promise<void> {
    this.update(s => ({ ...s, saving: { ...s.saving, label: true }, errors: { ...s.errors, label: '' } }));
    try {
      await indexService.saveIntentLabel(indexId, label);
      this.update(s => ({ ...s, saving: { ...s.saving, label: false } }));
    } catch (e) {
      this.update(s => ({ ...s, saving: { ...s.saving, label: false }, errors: { ...s.errors, label: String(e) } }));
    }
  }

  async saveMapping(indexId: string, cfg: MappingConfig): Promise<void> {
    this.update(s => ({ ...s, saving: { ...s.saving, mapping: true }, errors: { ...s.errors, mapping: '' } }));
    try {
      await indexService.saveMapping(indexId, cfg);
      this.update(s => ({ ...s, saving: { ...s.saving, mapping: false } }));
    } catch (e) {
      this.update(s => ({ ...s, saving: { ...s.saving, mapping: false }, errors: { ...s.errors, mapping: String(e) } }));
    }
  }

  reset(): void {
    this.set({ saving: { label: false, mapping: false }, errors: { label: '', mapping: '' } });
  }
}

export const mappingStore = new MappingStore();
