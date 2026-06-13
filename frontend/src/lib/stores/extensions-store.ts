import { StoreBase } from './StoreBase';
import { extensionService } from '../services/extensions/ExtensionService';
import type { ExtensionMeta, IndexExtension } from '../types';

interface ExtensionsState {
  /** Global extension library (all extensions, not scoped to an index). */
  library: ExtensionMeta[];
  /** Extensions assigned to the most recently loaded index. */
  indexExtensions: IndexExtension[];
  /** True while any async operation is in flight. */
  loading: boolean;
  /** Last error message; ``null`` when the last operation succeeded. */
  error: string | null;
}

/**
 * Store for the extension library and per-index extension assignments.
 *
 * ``library`` holds the global set of extensions; ``indexExtensions`` holds
 * the assignments for whichever index was most recently loaded.
 */
class ExtensionsStore extends StoreBase<ExtensionsState> {
  constructor() {
    super({ library: [], indexExtensions: [], loading: false, error: null });
  }

  /**
   * Load the global extension library and replace ``library`` in the store.
   * Does not affect ``indexExtensions``.
   */
  async loadLibrary(): Promise<void> {
    this.update(s => ({ ...s, loading: true, error: null }));
    try {
      const exts = await extensionService.listExtensions();
      const dtos: ExtensionMeta[] = exts.map(e => ({
        id: e.id, name: e.name, description: e.description,
        code: e.code, created_at: e.createdAt, updated_at: e.updatedAt,
      }));
      this.update(s => ({ ...s, library: dtos, loading: false }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }

  /**
   * Load the global library and the per-index assignments for ``indexId`` in parallel.
   * Replaces both ``library`` and ``indexExtensions`` in a single state update.
   */
  async loadForIndex(indexId: string): Promise<void> {
    this.update(s => ({ ...s, loading: true, error: null }));
    try {
      const [libExts, idxExts] = await Promise.all([
        extensionService.listExtensions(),
        extensionService.listIndexExtensions(indexId),
      ]);
      const library: ExtensionMeta[] = libExts.map(e => ({
        id: e.id, name: e.name, description: e.description,
        code: e.code, created_at: e.createdAt, updated_at: e.updatedAt,
      }));
      const indexExtensions: IndexExtension[] = idxExts.map(e => ({
        id: e.id, name: e.name, description: e.description, code: e.code,
        created_at: e.createdAt, updated_at: e.updatedAt,
        run_order: e.runOrder, enabled: e.enabled,
      }));
      this.update(s => ({ ...s, library, indexExtensions, loading: false }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }

  /**
   * Create a new extension and prepend it to the ``library`` list.
   *
   * @param name        Display name.
   * @param description Optional description.
   * @param code        JavaScript source (must define ``function transform(doc)``).
   */
  async createExtension(name: string, description: string, code: string): Promise<void> {
    const ext = await extensionService.createExtension(name, description, code);
    const dto: ExtensionMeta = {
      id: ext.id, name: ext.name, description: ext.description,
      code: ext.code, created_at: ext.createdAt, updated_at: ext.updatedAt,
    };
    this.update(s => ({ ...s, library: [dto, ...s.library] }));
  }

  /**
   * Update an existing extension and reflect the change in ``library``.
   *
   * @param fields  Only the supplied fields are changed.
   */
  async updateExtension(
    id: string,
    fields: Partial<Pick<ExtensionMeta, 'name' | 'description' | 'code'>>
  ): Promise<void> {
    const ext = await extensionService.updateExtension(id, fields);
    const dto: ExtensionMeta = {
      id: ext.id, name: ext.name, description: ext.description,
      code: ext.code, created_at: ext.createdAt, updated_at: ext.updatedAt,
    };
    this.update(s => ({
      ...s,
      library: s.library.map(e => (e.id === id ? { ...e, ...dto } : e)),
    }));
  }

  /**
   * Permanently delete an extension from the library.
   * The backend cascade-deletes any index assignments for this extension.
   */
  async deleteExtension(id: string): Promise<void> {
    await extensionService.deleteExtension(id);
    this.update(s => ({ ...s, library: s.library.filter(e => e.id !== id) }));
  }

  /**
   * Assign (or update) an extension on an index, then reload the per-index assignments.
   *
   * @param runOrder  Execution position — lower numbers run first.
   * @param enabled   When ``false`` the extension is skipped without being removed.
   */
  async assignExtension(
    indexId: string,
    extensionId: string,
    runOrder: number,
    enabled: boolean
  ): Promise<void> {
    await extensionService.assignExtension(indexId, extensionId, runOrder, enabled);
    await this.loadForIndex(indexId);
  }

  /**
   * Remove an extension assignment from an index, then reload the per-index assignments.
   * The extension remains in the global library and can be re-assigned later.
   */
  async unassignExtension(indexId: string, extensionId: string): Promise<void> {
    await extensionService.unassignExtension(indexId, extensionId);
    await this.loadForIndex(indexId);
  }
}

export const extensionsStore = new ExtensionsStore();
