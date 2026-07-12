import type { ExtensionMeta, IndexExtension } from '../types';
import { api } from '../api';

/**
 * Repository for all extension-related HTTP calls.
 *
 * Delegates directly to the ``api`` client; returns raw DTOs unchanged.
 */
export class ExtensionRepository {
  /** Fetch all extensions from the global library, newest first. */
  listExtensions(): Promise<ExtensionMeta[]> {
    return api.listExtensions();
  }

  /**
   * Create a new JS transform extension in the global library.
   *
   * @param name         Display name for the extension.
   * @param description  Optional human-readable description.
   * @param code         JavaScript source; must define ``function transform(doc) { ... return doc; }``.
   */
  createExtension(name: string, description: string, code: string, ext_type: 'transform' | 'pre_processor' = 'transform'): Promise<ExtensionMeta> {
    return api.createExtension(name, description, code, ext_type);
  }

  /**
   * Partially update an extension.
   *
   * @param fields  Only the supplied fields are changed.
   */
  updateExtension(
    id: string,
    fields: Partial<Pick<ExtensionMeta, 'name' | 'description' | 'code'>>
  ): Promise<ExtensionMeta> {
    return api.updateExtension(id, fields);
  }

  /**
   * Permanently delete an extension from the library.
   * The backend cascade-deletes any index assignments for this extension.
   */
  deleteExtension(id: string): Promise<Response> {
    return api.deleteExtension(id);
  }

  /** Fetch extensions assigned to an index, ordered by ``run_order``. */
  listIndexExtensions(indexId: string): Promise<IndexExtension[]> {
    return api.listIndexExtensions(indexId);
  }

  /**
   * Assign an extension to an index (or update an existing assignment).
   *
   * @param runOrder  Execution order — lower numbers run first.
   * @param enabled   When ``false`` the extension is skipped without being removed.
   */
  assignExtension(
    indexId: string,
    extensionId: string,
    runOrder: number,
    enabled: boolean
  ): Promise<{ ok: boolean }> {
    return api.assignExtension(indexId, extensionId, runOrder, enabled);
  }

  /**
   * Remove an extension assignment from an index.
   * The extension remains in the global library and can be re-assigned later.
   */
  unassignExtension(indexId: string, extensionId: string): Promise<Response> {
    return api.unassignExtension(indexId, extensionId);
  }
}

export const extensionRepository = new ExtensionRepository();
