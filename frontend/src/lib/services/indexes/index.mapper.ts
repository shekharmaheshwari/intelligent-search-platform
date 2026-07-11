import type { IndexMeta } from '../../types';
import type { Index } from './index.model';

/**
 * Map a single ``IndexMeta`` DTO to the ``Index`` domain model.
 *
 * Converts snake_case API fields to camelCase and makes ``isExternal`` non-optional
 * by defaulting it to ``false`` when the backend omits the field.
 */
export function mapIndexMetaToIndex(dto: IndexMeta): Index {
  return {
    id: dto.id,
    name: dto.name,
    description: dto.description,
    createdAt: dto.created_at,
    collectionName: dto.collection_name,
    connectors: dto.connectors,
    docCount: dto.doc_count,
    isExternal: dto.is_external ?? false,
    externalCollection: dto.external_collection,
    externalFilter: dto.external_filter,
    externalQueryBy: dto.external_query_by,
    externalTitleField: dto.external_title_field,
    externalUrlField: dto.external_url_field,
    embedModel: dto.embed_model ?? '',
    versionBoost: dto.version_boost ?? false,
    intentLabel: dto.intent_label ?? null,
    affinityDimensions: dto.affinity_dimensions ?? [],
  };
}

/**
 * Map an array of ``IndexMeta`` DTOs to ``Index`` domain models.
 * Convenience wrapper around ``mapIndexMetaToIndex``.
 */
export function mapIndexesToDomain(dtos: IndexMeta[]): Index[] {
  return dtos.map(mapIndexMetaToIndex);
}
