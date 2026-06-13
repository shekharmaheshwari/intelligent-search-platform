import type { ConnectorConfig, IngestStatus, SearchResult, DocumentItem } from '../../types';

export interface Index {
  id: string;
  name: string;
  description: string;
  createdAt: string;
  collectionName: string;
  connectors: ConnectorConfig[];
  docCount: number;
  isExternal: boolean;
  externalCollection?: string;
  externalFilter?: string;
  externalQueryBy?: string;
  externalTitleField?: string;
  externalUrlField?: string;
}

export type { IngestStatus, SearchResult, DocumentItem };
