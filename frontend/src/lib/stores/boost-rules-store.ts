import { StoreBase } from './StoreBase';
import { api } from '../api';
import type { BoostRule } from '../types';

interface BoostRulesState {
  rules:   BoostRule[];
  fields:  string[];
  loading: boolean;
  saving:  boolean;
  error:   string;
}

class BoostRulesStore extends StoreBase<BoostRulesState> {
  constructor() {
    super({ rules: [], fields: [], loading: false, saving: false, error: '' });
  }

  async load(indexId: string): Promise<void> {
    this.update(s => ({ ...s, loading: true, error: '' }));
    try {
      const [rulesRes, fieldsRes] = await Promise.all([
        api.getBoostRules(indexId),
        api.getBoostRuleFields(indexId),
      ]);
      this.update(s => ({
        ...s,
        rules:   rulesRes.rules,
        fields:  fieldsRes.fields,
        loading: false,
      }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }

  async save(indexId: string, rules: BoostRule[]): Promise<void> {
    this.update(s => ({ ...s, saving: true, error: '' }));
    try {
      const res = await api.updateBoostRules(indexId, rules);
      this.update(s => ({ ...s, rules: res.rules, saving: false }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), saving: false }));
    }
  }

  reset(): void {
    this.set({ rules: [], fields: [], loading: false, saving: false, error: '' });
  }
}

export const boostRulesStore = new BoostRulesStore();
