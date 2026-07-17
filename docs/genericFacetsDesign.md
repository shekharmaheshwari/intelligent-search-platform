# Generic Facet / Filter System

> **Status: Implemented.**  
> This document is the canonical reference for how facet filtering works end-to-end.

## Problem

The original implementation had facet fields hardcoded at every layer:

- `models/search.py` — `manufacturer: list[str]`, `category: list[str]`
- `routers/search.py` — explicit `if req.manufacturer: ...` blocks
- Frontend `FilterSidebar.svelte` — dedicated render blocks per field
- PLP `catalogController.ts` — named keys in the filter state

Adding a new facetable field (e.g. `customer_group`, `plan_tier`) required code changes in at least four files. This made the filter system an extension point that required engineering involvement to change.

---

## Solution

A single generic `filters: dict[str, list[str]]` field replaces all named filter fields. The backend loops over the dict; the frontend builds it from whatever facet state is active. Adding a new facetable field requires only enabling the **Facet** toggle in the Fields tab — no code changes.

---

## Backend

### `models/search.py` — SearchRequest

```python
class SearchRequest(BaseModel):
    q: str
    mode: str = "hybrid"
    page: int = 1
    per_page: int = 20
    filters: dict[str, list[str]] = {}
    action_history: list[CustomerAction] | None = None
    pinned_hits: list[str] | None = None
```

No named filter fields. `filters` carries every active facet selection.

### `routers/search.py` — filter_by construction

```python
_parts = [
    f"{field}:=[{','.join(values)}]"
    for field, values in (req.filters or {}).items()
    if values
]
filter_by = " && ".join(_parts) if _parts else None
```

`query_by` is also derived dynamically from `field_config`:

```python
_extra_qb = [
    fc["name"]
    for fc in (meta.field_config or [])
    if fc.get("searchable") and fc["name"] not in {"title", "content"}
]
ts_query_by = ("title,content," + ",".join(_extra_qb)) if _extra_qb else None
```

---

## Frontend (PLP)

### `FilterState` in PLP stores

```typescript
interface FilterState {
  tab: TabKey;
  filters: {
    category: Set<string>;
    manufacturer: Set<string>;
    price_range: [number, number] | null;
    facets: Record<string, Set<string>>;  // all other facet fields
  };
}
```

`category` and `manufacturer` remain as named keys in `FilterState` for backward compatibility with the dedicated UI sections, but they are merged into the generic `allFilters` dict before the API call.

### `catalogController.ts` — building allFilters

```typescript
const allFilters: Record<string, string[]> = {};
const cats = [...effectiveState.filters.category];
const mfrs = [...effectiveState.filters.manufacturer];
if (cats.length) allFilters['category']     = cats;
if (mfrs.length) allFilters['manufacturer'] = mfrs;
for (const [field, values] of Object.entries(effectiveState.filters.facets)) {
  const active = [...values];
  if (active.length) allFilters[field] = active;
}
// sent as: { filters: Object.keys(allFilters).length ? allFilters : undefined }
```

### `FilterSidebar.svelte` — dynamic rendering

Fields with dedicated UI sections are listed in `DEDICATED_FIELDS`:

```typescript
const DEDICATED_FIELDS = new Set(['category', 'manufacturer', 'price_range']);
```

Every other facetable field returned by the API is rendered in a generic loop:

```svelte
$: genericFacetEntries = Object.entries($facetsStore)
  .filter(([field]) => !DEDICATED_FIELDS.has(field) && items.length > 0);

{#each genericFacetEntries as [field, items] (field)}
  <div class="group">
    <h3 class="group-title">{fieldLabel(field)}</h3>
    {#each items as f}
      <label>
        <input type="checkbox"
               checked={$filterStore.facets?.[field]?.has(f.value)}
               on:change={() => toggleFacet(field, f.value)} />
        {f.value} <span class="count">({f.count})</span>
      </label>
    {/each}
  </div>
{/each}
```

`fieldLabel(field)` converts snake_case to Title Case:
`customer_group` → `Customer Group`.

### INDEX_FACET_TABS guard

Some tabs (e.g. `plans`) load their facets from the ISP index API rather than deriving them from the result set. A guard prevents `computeFacetsFromItems` from overwriting these:

```typescript
const INDEX_FACET_TABS = new Set<TabKey>(['plans']);

// in loadProducts:
if (!CATALOG_FACET_TYPE[tab] && !INDEX_FACET_TABS.has(tab)) {
  facetsStore.set(computeFacetsFromItems(...));
}
```

---

## PLP types.ts

```typescript
export interface IndexSearchParams {
  q: string;
  mode: string;
  page: number;
  per_page: number;
  filters?: Record<string, string[]>;
  action_history?: CustomerAction[];
  pinned_hits?: string[];
}
```

`manufacturer` and `category` were removed as named fields from `IndexSearchParams`. All filter fields flow through `filters`.

---

## File Reference

| File | Role |
|---|---|
| `backend/src/isp/models/search.py` | `filters: dict[str, list[str]]` on `SearchRequest` |
| `backend/src/isp/routers/search.py` | Generic `filter_by` construction loop; dynamic `query_by` from `field_config` |
| `plp/src/lib/types.ts` | Generic `filters?: Record<string, string[]>` in `IndexSearchParams` |
| `plp/src/lib/controller/catalogController.ts` | `allFilters` dict builder; `INDEX_FACET_TABS` guard |
| `plp/src/components/FilterSidebar.svelte` | `DEDICATED_FIELDS`, `toggleFacet()`, generic facet loop, `fieldLabel()` |

---

## References

1. **Baeza-Yates, R., & Ribeiro-Neto, B.** (2011). *Modern Information Retrieval* (2nd ed.). Addison-Wesley. ISBN: 978-0-321-41691-9 — Faceted search (Chapter 10) as a navigation paradigm; the generic filter system implements attribute-value faceting without requiring schema changes for new facet dimensions.

2. **Tunkelang, D.** (2009). *Faceted Search*. Morgan & Claypool. DOI: [10.2200/S00190ED1V01Y200904ICR005](https://doi.org/10.2200/S00190ED1V01Y200904ICR005) — Establishes the case for treating facet dimensions as data rather than code; the `filters: dict[str, list[str]]` design follows this principle directly.
