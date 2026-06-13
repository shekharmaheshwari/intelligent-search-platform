# Data Layer Architecture

**Last Updated**: 2026-06-13

This document defines the canonical data flow for the ISP frontend: how a component gets its data, how state is shared between components, and where each concern lives.

---

## Data Flow Overview

```
┌─────────────┐     reads / dispatches actions     ┌──────────────┐
│  Component  │ ◄──────────────────────────────── │    Store     │
│  (.svelte)  │ ──────────────────────────────── ► │  (reactive)  │
└──────┬──────┘                                    └──────┬───────┘
       │ uses                                             │ calls
       ▼                                                  ▼
┌─────────────┐                                    ┌──────────────┐
│   Helper    │                                    │   Service    │
│ (.helpers   │                                    │ (business    │
│    .ts)     │                                    │   logic)     │
└─────────────┘                                    └──────┬───────┘
                                                          │ calls
                                                          ▼
                                                   ┌──────────────┐
                                                   │   Mapper     │
                                                   │ (DTO →       │
                                                   │  domain)     │
                                                   └──────┬───────┘
                                                          │ uses
                                                          ▼
                                                   ┌──────────────┐
                                                   │  Repository  │
                                                   │ (HTTP / BFF) │
                                                   └──────────────┘
```

**Rule**: data flows in one direction. A component never calls a repository directly. A repository never knows about stores or the UI. Helpers are leaf nodes — they have no dependencies on any other layer.

---

## Core Layers

| Layer          | Responsibility                                                                                      | Lives in                                             |
| -------------- | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| **Types**      | DTO shapes (API contract) and UI state types — split by domain, re-exported from a barrel index     | `src/lib/types/`                                     |
| **Repository** | Raw HTTP calls — returns unmodified DTOs. No mapping, no business logic, no store access            | `src/lib/repository/`                                |
| **Mapper**     | Pure functions: DTO → domain model. No side effects, no async                                       | `src/lib/services/<domain>/<domain>.mapper.ts`       |
| **Service**    | Calls repository, applies mapper, owns orchestration. No direct HTTP                                | `src/lib/services/<domain>/<Domain>Service.ts`       |
| **Store**      | Svelte reactive state extending `StoreBase<T>`. Singleton exports. Immutability rule: always spread | `src/lib/stores/`                                    |
| **Component**  | UI only — reads store via `$store`, calls store action methods. Never calls repository or service   | `src/components/<domain>/` (`layout/`, `sources/`, `extensions/`, `settings/`) |
| **Helper**     | Pure functions co-located with the component that owns them. No side effects, no async              | `src/components/<domain>/<ComponentName>.helpers.ts` |

---

## Layer Contracts

### Types

All DTO and UI-state types live in `src/lib/types/`, split by domain. A barrel `index.ts` re-exports everything so existing imports never need to change.

```
src/lib/types/
├── index.types.ts      ← IndexMeta, ConnectorConfig, RestConnectorConfig,
│                          SitemapConnectorConfig, IngestStatus, SearchResult, DocumentItem
├── extension.types.ts  ← ExtensionMeta, IndexExtension
├── settings.types.ts   ← EmbedModelOption, AppSettings
├── app.types.ts        ← View, AppState
└── index.ts            ← barrel: export * from each file above
```

**Two categories of types live here:**

| Category       | Description                                                                        | Naming convention  |
| -------------- | ---------------------------------------------------------------------------------- | ------------------ |
| **DTO types**  | Mirror the backend Pydantic models exactly — snake_case fields, raw API shapes     | `IndexMeta`, `AppSettings`, … |
| **UI types**   | Frontend-only state shapes that never touch the network                            | `View`, `AppState` |

**Domain model types** (camelCase, UI-friendly) live alongside their mapper in `src/lib/services/<domain>/<domain>.model.ts` — not in this folder. The repository returns DTO types; the mapper converts them to domain models.

**Rules:**
- Always import from the barrel: `import type { IndexMeta } from '../lib/types'`.
- When adding a new field to an existing type, edit the appropriate domain file (e.g. `index.types.ts`), not `index.ts`.
- When adding a completely new domain, create a new `<domain>.types.ts` file and add `export * from './<domain>.types'` to `index.ts`.
- Never put domain model types (the camelCase shapes produced by mappers) in this folder — those belong in `src/lib/services/<domain>/<domain>.model.ts`.

```typescript
// ✅ correct — import from barrel, DTO type used in repository layer
import type { IndexMeta, IngestStatus } from '../lib/types';

// ✅ correct — domain model type imported from its own model file
import type { Index } from '../lib/services/indexes/index.model';

// ❌ wrong — importing a domain model type from the types folder
import type { Index } from '../lib/types';
```

### Repository

- Calls `api.*` fetch helpers and returns the raw response unchanged.
- **No mapping, no business logic, no store access.**
- All return types are DTO types from `src/lib/types/`.
- Exported as a module-level singleton so it can be injected into services.

```typescript
// src/lib/repository/IndexRepository.ts
import { api } from '../api';
import type { IndexMeta } from '../types';

class IndexRepository {
  async getIndex(id: string): Promise<IndexMeta> {
    return api.getIndex(id); // ← raw DTO, no transformation
  }
}

export const indexRepository = new IndexRepository();
```

### Mapper

- Pure functions only — no side effects, no async, no class state.
- Input: DTO type from `src/lib/types/`.
- Output: domain model type defined alongside the mapper in `<domain>.model.ts`.

```typescript
// src/lib/services/indexes/index.mapper.ts
import type { IndexMeta } from '../../types';
import type { Index } from './index.model';

export function mapIndexMetaToIndex(dto: IndexMeta): Index {
  return {
    id: dto.id,
    name: dto.name,
    createdAt: dto.created_at,      // snake_case → camelCase
    collectionName: dto.collection_name,
    docCount: dto.doc_count,
    isExternal: dto.is_external ?? false,
    connectors: dto.connectors,
  };
}
```

### Service

- Calls the repository, applies the mapper, returns domain models.
- Owns orchestration (parallel calls, error normalisation).
- **No direct HTTP — all network goes through the repository.**
- Exported as a module-level singleton.

```typescript
// src/lib/services/indexes/IndexService.ts
import { indexRepository } from '../../repository/IndexRepository';
import { mapIndexMetaToIndex } from './index.mapper';
import type { Index } from './index.model';

class IndexService {
  async getIndex(id: string): Promise<Index> {
    const dto = await indexRepository.getIndex(id);
    return mapIndexMetaToIndex(dto);
  }
}

export const indexService = new IndexService();
```

### Store

- Extends `StoreBase<T>` (wraps Svelte `writable`).
- Exposes action methods that call the service layer, then call `this.set()` or `this.update()`.
- Exported as a module-level singleton — every component that subscribes gets the same instance.

**Immutability rule — always replace, never mutate:**

Svelte's writable store triggers subscribers only when `set`/`update` is called with a new state reference. Mutating a nested object in place is invisible to the store — components will not re-render.

```typescript
// ❌ invisible to the store — components do NOT re-render
const idx = indexesStore.getValue().indexes.find(i => i.id === id);
idx.name = 'new name';

// ✅ new object reference — all subscribers update
this.update(s => ({
  ...s,
  indexes: s.indexes.map(i => (i.id === id ? { ...i, name: 'new name' } : i)),
}));
```

```typescript
// src/lib/stores/indexes-store.ts
import { StoreBase } from './StoreBase';
import { indexService } from '../services/indexes/IndexService';
import type { IndexMeta } from '../types';

interface IndexesState {
  indexes: IndexMeta[];
  loading: boolean;
  error: string | null;
}

class IndexesStore extends StoreBase<IndexesState> {
  constructor() {
    super({ indexes: [], loading: false, error: null });
  }

  async load(): Promise<void> {
    this.set({ ...this.getValue(), loading: true, error: null });
    const indexes = await indexService.listIndexes();
    this.set({ indexes, loading: false, error: null });
  }
}

export const indexesStore = new IndexesStore();
```

**Two components sharing the same store:**

```svelte
<!-- SourcesSidebar.svelte -->
<script lang="ts">
  import { indexesStore } from '../lib/store';
  $: indexes = $indexesStore.indexes;
</script>
{#each indexes as idx}...{/each}

<!-- App.svelte -->
<script lang="ts">
  import { indexesStore } from '../lib/store';
  $: loading = $indexesStore.loading;
</script>
{#if loading}<Spinner />{/if}
```

When `indexesStore.load()` completes, both components re-render automatically.

### Component

- Uses `$: x = $store.field` (Svelte 4 reactive statement + auto-subscription) to read reactive values.
- Calls store action methods for mutations — **never calls a repository directly**.
- For transient data not shared across components (search results, document list), calling the service directly from the component is acceptable.
- Has no knowledge of HTTP, DTOs, or mapping.

```svelte
<script lang="ts">
  import { indexesStore, appStateStore } from '../lib/store';

  $: indexes = $indexesStore.indexes;
  $: loading = $indexesStore.loading;

  function selectIndex(id: string) {
    appStateStore.selectIndex(id); // ← store action, not a service call
  }
</script>
```

### Helper

- Pure functions co-located with the component that owns them — `<ComponentName>.helpers.ts` in the same subfolder.
- No side effects, no async, no store or service access.
- **Scope rule**: if the same function is needed by more than one unrelated component, extract it to a shared utility module.

```typescript
// src/components/sources/SourcesSidebar.helpers.ts
export function connectorType(idx: IndexMeta): string {
  if (idx.is_external) return 'external';
  const types = idx.connectors.map(c => c.type);
  return types[0] ?? 'unknown';
}

export function shortUrl(url: string): string {
  return url.replace(/^https?:\/\//, '').replace(/\/$/, '');
}
```

---

## File Layout

```
src/
  lib/
    types/                            ← all DTO + UI-state type definitions
      index.types.ts                  ← index/source DTOs (IndexMeta, ConnectorConfig, …)
      extension.types.ts              ← extension DTOs (ExtensionMeta, IndexExtension)
      settings.types.ts               ← settings DTOs (EmbedModelOption, AppSettings)
      app.types.ts                    ← UI state types (View, AppState)
      index.ts                        ← barrel re-export (import from here always)

    repository/                       ← raw HTTP calls, return DTOs unchanged
      IndexRepository.ts
      ExtensionRepository.ts
      SettingsRepository.ts

    services/
      indexes/
        index.model.ts                ← domain model: Index (camelCase, UI-friendly)
        index.mapper.ts               ← mapIndexMetaToIndex(), mapIndexesToDomain()
        IndexService.ts               ← calls repository, applies mapper
      extensions/
        extension.model.ts            ← Extension, IndexExtensionModel
        extension.mapper.ts           ← mapExtensionMetaToExtension(), …
        ExtensionService.ts
      settings/
        settings.model.ts             ← Settings (camelCase)
        settings.mapper.ts            ← mapAppSettingsToSettings()
        SettingsService.ts

    stores/
      StoreBase.ts                    ← abstract class wrapping Svelte writable<T>
      app-state-store.ts              ← navigation state (view, selectedIndexId, detailTab)
      indexes-store.ts                ← global index list
      extensions-store.ts             ← extension library + per-index assignments
      settings-store.ts               ← application settings

    store.ts                          ← barrel re-export of all store singletons
    api.ts                            ← low-level fetch wrapper (used only by repositories)

  components/
    layout/                           ← app shell, always visible
      App.svelte                      ← root layout, subscribes to appStateStore / indexesStore
      Header.svelte                   ← logo + nav bar

    sources/                          ← index/source management feature
      Dashboard.svelte                ← empty-state or index grid
      IndexCard.svelte                ← single card in the dashboard grid
      SourcesSidebar.svelte           ← left-rail source list
      SourcesSidebar.helpers.ts       ← connectorUrl(), connectorType(), shortUrl()
      SourceContent.svelte            ← main content pane for a selected source
      SourceContent.helpers.ts        ← excerpt(), fmtDate(), docFields(), …
      IndexDetail.svelte              ← configure / overview tab host
      IndexDetail.helpers.ts          ← fmtDate()
      DocumentTable.svelte            ← paginated document list
      DocumentTable.helpers.ts        ← fmtDate()
      SearchPanel.svelte              ← search UI within a source
      SearchPanel.helpers.ts          ← excerpt()
      ApiPanel.svelte                 ← copy-ready curl examples for a source
      RebuildPanel.svelte             ← rebuild / re-ingest workflow
      RebuildPanel.helpers.ts         ← parseCurl(), buildCurl(), getExistingRest()
      CreateWizard.svelte             ← multi-step new-source wizard
      CreateWizard.helpers.ts         ← embedModelOption(), embedDimWillChange()

    extensions/                       ← extension library + per-index assignments
      ExtensionsPanel.svelte          ← global extension library management
      ExtensionsPanel.helpers.ts      ← fmtDate()
      IndexExtensionsPanel.svelte     ← per-index extension assignment + ordering

    settings/                         ← embedding model configuration
      SettingsPanel.svelte            ← model picker + API key form
      SettingsPanel.helpers.ts        ← modelOption(), effectiveDim(), dimensionWillChange()
```

---

## Anti-Patterns

| Pattern                                                                   | Why It Is Wrong                                                                                         | Correct Alternative                                                                      |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Component calls `api.*` directly                                          | Bypasses repository + service + mapper; component now knows about raw HTTP and DTOs                     | Call a store action method, or call the service directly for transient per-component data |
| Component calls a repository directly                                     | Bypasses service and mapper; component receives raw DTO types                                           | Component calls service (for local data) or reads from a store (for shared state)        |
| Service calls `this.set()` on a store                                     | Service layer must not know about UI state                                                              | Service returns domain model; the store action calls `this.set()`                        |
| Repository maps DTO to domain model                                       | Mapping belongs in the service layer                                                                    | Return raw DTO from repository; map inside the service                                   |
| Adding a new type directly to `src/lib/types/index.ts`                    | Barrel file should only contain `export *` re-exports                                                   | Add the type to the appropriate domain file (e.g. `index.types.ts`)                      |
| Putting a domain model type (camelCase) in `src/lib/types/`               | Domain model types belong with their mapper, not alongside DTO types                                    | Define in `src/lib/services/<domain>/<domain>.model.ts`                                  |
| Two components each call the same service endpoint independently          | Duplicate network calls; state gets out of sync                                                         | One store backed by one service; both components subscribe to the store                  |
| Mapper imported and called directly inside a component                    | Component now depends on DTO types; tight coupling to API shape                                         | Store or service exposes mapped domain model; component reads that                        |
| Mutating a nested object in store state in place (`state.indexes[0].name = 'x'`) | Store reference unchanged — subscribers do not re-render                              | Call a store action that spreads a new object: `this.update(s => ({ ...s, ... }))`       |

---

## Adding a New Domain

1. **Types** — create `src/lib/types/<domain>.types.ts` with all DTO interfaces (snake_case fields mirroring the backend). Add `export * from './<domain>.types'` to `src/lib/types/index.ts`.
2. **Repository** — create `src/lib/repository/<Domain>Repository.ts`; call `api.*` methods and return raw DTOs. Export as a singleton (`export const <domain>Repository = new <Domain>Repository()`).
3. **Domain model** — create `src/lib/services/<domain>/<domain>.model.ts` with the camelCase domain model interface.
4. **Mapper** — create `src/lib/services/<domain>/<domain>.mapper.ts` with pure mapping functions (DTO → domain model). No async, no side effects.
5. **Service** — create `src/lib/services/<domain>/<Domain>Service.ts`; call the repository, apply the mapper, return domain models. Export as a singleton.
6. **Store** (if global shared state is needed) — create `src/lib/stores/<domain>-store.ts` extending `StoreBase<T>`; expose action methods that call the service and update state with `this.set()`/`this.update()`. Export as a singleton and add to the `src/lib/store.ts` barrel.
7. **Component** — place the component in the matching subfolder (`layout/`, `sources/`, `extensions/`, or `settings/`). Import the store singleton; read reactive values with `$: x = $<domain>Store.field`; call store action methods for mutations. For transient per-component data (not shared), call the service directly from the component instead of creating a store. When importing from `src/lib/`, use `../../lib/` (one extra level up because components are now one directory deeper).
8. **Helpers** — create `<ComponentName>.helpers.ts` in the same subfolder as the component. Extract any pure formatting or derivation functions there.
