import { StoreBase } from './StoreBase';
import { api } from '../api';
import type { ChatPersonaConfig } from '../types';

const DEFAULTS: ChatPersonaConfig = {
  item_noun:               'product',
  enable_comparison:       true,
  additional_instructions: '',
};

interface ChatPersonaState {
  config:  ChatPersonaConfig;
  loading: boolean;
  saving:  boolean;
  error:   string;
}

class ChatPersonaStore extends StoreBase<ChatPersonaState> {
  constructor() {
    super({ config: DEFAULTS, loading: false, saving: false, error: '' });
  }

  async load(indexId: string): Promise<void> {
    this.update(s => ({ ...s, loading: true, error: '' }));
    try {
      const config = await api.getChatPersona(indexId);
      this.update(s => ({ ...s, config, loading: false }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }

  async save(indexId: string, config: ChatPersonaConfig): Promise<void> {
    this.update(s => ({ ...s, saving: true, error: '' }));
    try {
      const saved = await api.updateChatPersona(indexId, config);
      this.update(s => ({ ...s, config: saved, saving: false }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), saving: false }));
    }
  }

  reset(): void {
    this.set({ config: DEFAULTS, loading: false, saving: false, error: '' });
  }
}

export const chatPersonaStore = new ChatPersonaStore();
