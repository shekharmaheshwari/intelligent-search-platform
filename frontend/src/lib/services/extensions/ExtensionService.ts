import { extensionRepository, type ExtensionRepository } from '../../repository/ExtensionRepository';
import {
  mapExtensionsToLibrary,
  mapExtensionMetaToExtension,
  mapIndexExtensionsToModels,
} from './extension.mapper';
import type { Extension, IndexExtensionModel } from './extension.model';
import type { ExtensionMeta } from '../../types';

/**
 * Service for all extension-domain operations.
 *
 * Calls the repository, applies the mapper, and returns domain models.
 * No direct HTTP — all network I/O goes through ``ExtensionRepository``.
 */
export class ExtensionService {
  constructor(private readonly repo: ExtensionRepository) {}

  /** Load the global extension library as domain models. */
  async listExtensions(): Promise<Extension[]> {
    const dtos = await this.repo.listExtensions();
    return mapExtensionsToLibrary(dtos);
  }

  /**
   * Create a new JS transform extension and return it as a domain model.
   *
   * @param name        Display name.
   * @param description Optional human-readable description.
   * @param code        JavaScript source; must define ``function transform(doc) { ... return doc; }``.
   */
  async createExtension(name: string, description: string, code: string, ext_type: 'transform' | 'pre_processor' = 'transform'): Promise<Extension> {
    const dto = await this.repo.createExtension(name, description, code, ext_type);
    return mapExtensionMetaToExtension(dto);
  }

  /**
   * Partially update an extension and return the updated domain model.
   *
   * @param fields  Only the supplied fields are changed; omitted fields are unchanged.
   */
  async updateExtension(
    id: string,
    fields: Partial<Pick<ExtensionMeta, 'name' | 'description' | 'code'>>
  ): Promise<Extension> {
    const dto = await this.repo.updateExtension(id, fields);
    return mapExtensionMetaToExtension(dto);
  }

  /**
   * Permanently delete an extension from the library.
   * The backend cascade-deletes any index assignments for this extension.
   */
  async deleteExtension(id: string): Promise<void> {
    await this.repo.deleteExtension(id);
  }

  /** Load extensions assigned to an index, ordered by ``run_order``. */
  async listIndexExtensions(indexId: string): Promise<IndexExtensionModel[]> {
    const dtos = await this.repo.listIndexExtensions(indexId);
    return mapIndexExtensionsToModels(dtos);
  }

  /**
   * Assign an extension to an index (or update an existing assignment).
   *
   * @param runOrder  Execution position — lower numbers run first.
   * @param enabled   When ``false`` the extension is skipped without being removed.
   */
  assignExtension(
    indexId: string,
    extensionId: string,
    runOrder: number,
    enabled: boolean
  ): Promise<{ ok: boolean }> {
    return this.repo.assignExtension(indexId, extensionId, runOrder, enabled);
  }

  /**
   * Remove an extension assignment from an index.
   * The extension remains in the global library and can be re-assigned later.
   */
  async unassignExtension(indexId: string, extensionId: string): Promise<void> {
    await this.repo.unassignExtension(indexId, extensionId);
  }
}

export const extensionService = new ExtensionService(extensionRepository);
