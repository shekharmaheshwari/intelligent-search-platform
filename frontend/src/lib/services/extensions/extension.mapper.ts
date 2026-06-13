import type { ExtensionMeta, IndexExtension } from '../../types';
import type { Extension, IndexExtensionModel } from './extension.model';

/**
 * Map a single ``ExtensionMeta`` DTO to the ``Extension`` domain model.
 * Converts snake_case timestamp fields to camelCase.
 */
export function mapExtensionMetaToExtension(dto: ExtensionMeta): Extension {
  return {
    id: dto.id,
    name: dto.name,
    description: dto.description,
    code: dto.code,
    createdAt: dto.created_at,
    updatedAt: dto.updated_at,
  };
}

/**
 * Map an array of ``ExtensionMeta`` DTOs to ``Extension`` domain models.
 * Convenience wrapper around ``mapExtensionMetaToExtension``.
 */
export function mapExtensionsToLibrary(dtos: ExtensionMeta[]): Extension[] {
  return dtos.map(mapExtensionMetaToExtension);
}

/**
 * Map a single ``IndexExtension`` DTO (which includes assignment fields) to
 * the ``IndexExtensionModel`` domain model.
 * Converts snake_case fields (``run_order``) to camelCase.
 */
export function mapIndexExtensionToModel(dto: IndexExtension): IndexExtensionModel {
  return {
    id: dto.id,
    name: dto.name,
    description: dto.description,
    code: dto.code,
    createdAt: dto.created_at,
    updatedAt: dto.updated_at,
    runOrder: dto.run_order,
    enabled: dto.enabled,
  };
}

/**
 * Map an array of ``IndexExtension`` DTOs to ``IndexExtensionModel`` domain models.
 * Convenience wrapper around ``mapIndexExtensionToModel``.
 */
export function mapIndexExtensionsToModels(dtos: IndexExtension[]): IndexExtensionModel[] {
  return dtos.map(mapIndexExtensionToModel);
}
