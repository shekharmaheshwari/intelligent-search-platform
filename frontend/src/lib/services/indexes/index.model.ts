import type { ConnectorConfig, IngestStatus, SearchResult, DocumentItem, AffinityDimension } from '../../types';

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
  /** Per-index embedding model override. Empty string means "use the global default". */
  embedModel?: string;
  /** When true, newer product generations receive a small relevance boost at search time. */
  versionBoost?: boolean;
  /** Inferred intent category label; null when the backend has not yet classified the index. */
  intentLabel?: string | null;
  /** URL-friendly identifier — usable anywhere a hex id is accepted in the public API. */
  slug: string;
  /** Affinity dimensions discovered after the last rebuild. */
  affinityDimensions?: AffinityDimension[];
}

export type { AffinityDimension };

export type { IngestStatus, SearchResult, DocumentItem };
