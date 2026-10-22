// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { StoreBase } from './StoreBase';
import { api } from '../api';
import type { AffinityDimension, ViewedItemPolicy } from '../types';

interface PersonalisationState {
  dims:       AffinityDimension[];
  policy:     ViewedItemPolicy;
  loading:    boolean;
  saving:     boolean;
  discovering: boolean;
  error:      string;
}

const DEFAULT_POLICY: ViewedItemPolicy = {
  viewed:    { mode: 'penalise', value: 0.05 },
  purchased: { mode: 'penalise', value: 0.10 },
};

class PersonalisationStore extends StoreBase<PersonalisationState> {
  constructor() {
    super({ dims: [], policy: DEFAULT_POLICY, loading: false, saving: false, discovering: false, error: '' });
  }

  async load(indexId: string): Promise<void> {
    this.update(s => ({ ...s, loading: true, error: '' }));
    try {
      const res = await api.getAffinityDimensions(indexId);
      this.update(s => ({
        ...s,
        dims:    res.dimensions ?? [],
        policy:  res.viewed_item_policy ?? DEFAULT_POLICY,
        loading: false,
      }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }

  async save(indexId: string, dims: AffinityDimension[], policy: ViewedItemPolicy): Promise<void> {
    this.update(s => ({ ...s, saving: true, error: '' }));
    try {
      const res = await api.updateAffinityDimensions(indexId, dims, policy);
      this.update(s => ({
        ...s,
        dims:   res.dimensions,
        policy: res.viewed_item_policy,
        saving: false,
      }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), saving: false }));
    }
  }

  async rediscover(indexId: string): Promise<void> {
    this.update(s => ({ ...s, discovering: true, error: '' }));
    try {
      const res = await api.rediscoverAffinityDimensions(indexId);
      this.update(s => ({
        ...s,
        dims:        res.dimensions,
        policy:      res.viewed_item_policy ?? s.policy,
        discovering: false,
      }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), discovering: false }));
    }
  }

  reset(): void {
    this.set({ dims: [], policy: DEFAULT_POLICY, loading: false, saving: false, discovering: false, error: '' });
  }
}

export const personalisationStore = new PersonalisationStore();
