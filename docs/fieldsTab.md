# Fields Tab — Implementation Reference

> **Status: Implemented and deployed.**  
> This document serves as the canonical reference for the Fields tab feature.

## Overview

The **Fields** tab in the index detail view surfaces every Typesense field
associated with an index and lets operators configure three per-field properties
through toggles — no Python scripts, no Typesense API calls needed.

---

## Tab placement

Current tab order in `IndexDetail.svelte`:

```
Overview | Connector | Mapping | Documents | Search | API | Suggestions | Personalisation | Boost Rules
```

New order (Fields inserted after Mapping):

```
Overview | Connector | Mapping | Fields | Documents | Search | API | Suggestions | Personalisation | Boost Rules
```

**Tab key:** `'fields'`  
**Display label:** `Fields`  
**Component:** `FieldsTab.svelte`

---

## What the tab shows

A table of every field in the Typesense collection schema, one row per field.

### Columns

| Column | Description |
|---|---|
| **Field name** | The Typesense field name (e.g. `category`, `customer_group`) |
| **Type** | Typesense type — `string`, `string[]`, `int32`, `float`, `bool`, etc. |
| **Searchable** | Toggle — whether this field is included in `query_by` for BM25/hybrid text search |
| **Facet** | Toggle — whether this field has `facet: true` in the Typesense schema |
| **Multi-value** | Toggle — whether the field stores an array of values (`string[]`), enabling multi-value faceting |

### System fields (read-only, shown greyed out)

These fields are internal to ISP and their properties cannot be changed:

```
id, vec, indexed_at, metadata, content, url, is_featured, popularity,
version_score, in_stock, price, boost_queries_equal, boost_queries_contains,
images, skus, color_options, aliases, tags
```

### Empty state

If the index has never been built (no Typesense collection exists yet), the tab
shows:

> _No fields yet. Run a rebuild to populate the schema._

---

## How each toggle works

### Facet toggle

Changes `facet: true/false` on the field.

**Constraint:** Typesense does not allow flipping `facet` in-place on an existing
field. The backend must drop the field and re-add it with the new value. Typesense
re-derives the stored values from existing documents automatically — no data loss.

**Applies:** immediately via a live schema update. No rebuild required.

### Multi-value toggle

Changes the field type between `string` (single value) and `string[]` (array).

**Constraint:** Same drop+re-add requirement as Facet. If a field currently holds
single strings and is switched to `string[]`, existing documents that have a scalar
value are automatically coerced. If switched back to `string`, the first array
element is kept.

**Applies:** immediately via a live schema update. No rebuild required.

### Searchable toggle

Controls whether the field appears in the `query_by` list passed to Typesense on
every search call. Does NOT change the Typesense schema — this is a search-time
setting stored in ISP index metadata.

**Applies:** immediately. Next search call picks up the updated `query_by` list.

Default `query_by` fields (always included, not toggleable): `title`, `content`.
Additional fields enabled via the Searchable toggle are appended.

---

## Persistence across rebuilds

Field configuration must survive a rebuild (which drops and recreates the
Typesense collection). Settings are stored in the ISP index metadata document
alongside existing settings like `boost_rules` and `affinity_dimensions`.

### New metadata field: `field_config`

```json
{
  "field_config": [
    { "name": "customer_group", "facet": true,  "multi_value": false, "searchable": false },
    { "name": "plan_tier",      "facet": true,  "multi_value": false, "searchable": false },
    { "name": "tags",           "facet": false, "multi_value": true,  "searchable": false }
  ]
}
```

Only fields that have been explicitly configured appear in this list. Fields not in
the list fall back to their schema defaults after a rebuild.

### Rebuild pipeline integration

After a rebuild creates the collection and indexes all documents, the pipeline reads
`field_config` and applies each entry via the drop+re-add pattern. This is the same
step that currently runs manually (e.g. the Python snippet we ran to make
`customer_group` facetable). It will now run automatically on every rebuild.

---

## Backend changes

### 1. New endpoint — get all fields

```
GET /indexes/{idx_id}/fields
```

Returns every field in the Typesense schema with its current properties plus the
stored `field_config` overlay:

```json
{
  "fields": [
    {
      "name": "customer_group",
      "type": "string",
      "facet": true,
      "multi_value": false,
      "searchable": false,
      "system": false
    },
    {
      "name": "vec",
      "type": "float[]",
      "facet": false,
      "multi_value": true,
      "searchable": false,
      "system": true
    }
  ]
}
```

**File:** `backend/src/isp/routers/indexes.py`

### 2. New endpoint — update a single field

```
PATCH /indexes/{idx_id}/fields/{field_name}
```

Request body:

```json
{ "facet": true, "multi_value": false, "searchable": false }
```

The handler:
1. Validates the field exists in the Typesense schema
2. Rejects updates to system fields
3. If `facet` or `multi_value` changed — does drop+re-add on the live Typesense collection
4. Saves the new config into `field_config` in ISP metadata
5. Returns the updated field object

**File:** `backend/src/isp/routers/indexes.py`

### 3. New typesense_client helper — get full schema

```python
def get_all_fields(index_id: str) -> list[dict]:
    """Return all fields from the collection schema with name, type, facet, optional."""
```

**File:** `backend/src/isp/services/infra/typesense_client.py`

### 4. New typesense_client helper — update field properties

```python
def update_field_properties(index_id: str, field_name: str, facet: bool, multi_value: bool) -> None:
    """Drop and re-add a field to change its facet/type properties."""
```

This performs:
```python
client.collections[name].update({'fields': [{'name': field_name, 'drop': True}]})
client.collections[name].update({'fields': [{
    'name': field_name,
    'type': 'string[]' if multi_value else 'string',
    'optional': True,
    'facet': facet,
}]})
```

**File:** `backend/src/isp/services/infra/typesense_client.py`

### 5. IndexMeta model — add field_config

```python
field_config: list[FieldConfigEntry] = Field(default_factory=list)
```

```python
class FieldConfigEntry(BaseModel):
    name:        str
    facet:       bool = False
    multi_value: bool = False
    searchable:  bool = False
```

**File:** `backend/src/isp/models/index.py`

### 6. Rebuild pipeline — apply field_config

After the rebuild indexes all documents, iterate `meta.field_config` and call
`update_field_properties` for each entry that differs from the schema default.

**File:** `backend/src/isp/routers/indexes.py` (`_run_rebuild`)

### 7. Search — honour searchable fields

In `_run_search`, derive `query_by` from `meta.field_config`:

```python
extra_query_by = [
    f.name for f in (meta.field_config or []) if f.searchable
]
query_by = ",".join(["title", "content"] + extra_query_by)
```

Pass `query_by` into `ts.search_with_vec` and `ts.search_documents`.

**File:** `backend/src/isp/routers/search.py`

---

## Frontend changes

### 1. New component — `FieldsTab.svelte`

Located at `frontend/src/components/sources/FieldsTab.svelte`.

**Layout:**

```
┌──────────────────────────────────────────────────────────────────┐
│  Fields                                              [i] info     │
│  Configure search and filter behaviour per field.                 │
│  Facet and multi-value changes apply immediately.                 │
│  Searchable changes take effect on the next search.               │
├─────────────────┬────────────┬─────────────┬────────┬────────────┤
│  Field          │  Type      │  Searchable │  Facet │  Multi     │
├─────────────────┼────────────┼─────────────┼────────┼────────────┤
│  category       │  string    │  ○          │  ●     │  ○         │
│  customer_group │  string    │  ○          │  ●     │  ○         │
│  plan_tier      │  string    │  ○          │  ●     │  ○         │
│  title          │  string    │  ● (fixed)  │  ○     │  ○         │
│  content        │  string    │  ● (fixed)  │  ○     │  ○         │
│  id             │  string    │  — (system) │  —     │  —         │
│  vec            │  float[]   │  — (system) │  —     │  —         │
└─────────────────┴────────────┴─────────────┴────────┴────────────┘
```

- System fields are shown with `—` (em dash) in toggle columns and are not
  interactive
- `title` and `content` show Searchable as permanently on (non-toggleable)
- Toggling any property calls the PATCH endpoint immediately (optimistic UI with
  rollback on error)
- A spinner/loading state per row while the PATCH is in flight

### 2. IndexDetail.svelte changes

- Import `FieldsTab`
- Add `'fields'` to the `tab` union type and the `tabs` array
- Add `{:else if tab === 'fields'}<FieldsTab {indexId} />{/if}` to the tab body

### 3. IndexService — new methods

```typescript
getFields(indexId: string): Promise<{ fields: FieldEntry[] }>
updateField(indexId: string, fieldName: string, config: Partial<FieldConfig>): Promise<FieldEntry>
```

---

## Data flow summary

```
[Fields Tab loads]
  → GET /indexes/{id}/fields
  → typesense_client.get_all_fields()   ← reads live Typesense schema
  + IndexMeta.field_config              ← overlays stored ISP settings
  → renders table

[User toggles Facet on customer_group]
  → PATCH /indexes/{id}/fields/customer_group  { facet: true }
  → typesense_client.update_field_properties() ← drop+re-add in Typesense
  → IndexMeta.field_config updated in PostgreSQL
  → UI row updates optimistically

[Rebuild runs]
  → collection dropped + recreated
  → documents indexed
  → meta.field_config iterated
  → update_field_properties() called for each configured field
  → schema matches stored settings
```

---

## File Reference

| File | Role |
|---|---|
| `backend/src/isp/models/index.py` | `field_config: list[dict]` on `IndexMeta` |
| `backend/src/isp/routers/indexes.py` | `GET /{id}/fields`, `PATCH /{id}/fields/{name}`; `_FIELD_SYSTEM`, `_FIELD_ALWAYS_SEARCHABLE` constants; `field_config` re-applied in `_run_rebuild` |
| `backend/src/isp/routers/search.py` | Derives `ts_query_by` from `field_config` at request time |
| `backend/src/isp/services/infra/typesense_client.py` | `get_all_fields()`, `update_field_properties()`, `query_by` param on `search_documents` and `search_with_vec` |
| `frontend/src/components/sources/FieldsTab.svelte` | Fields tab UI with toggle controls |
| `frontend/src/components/sources/IndexDetail.svelte` | Mounts `<FieldsTab>` on `tab === 'fields'` |
| `frontend/src/lib/services/indexes/IndexService.ts` | `getFields()`, `updateField()` |
| `frontend/src/lib/repository/IndexRepository.ts` | `getFields()`, `updateField()` HTTP delegation |
| `frontend/src/lib/api.ts` | `getFields()`, `updateField()` fetch calls |
| `frontend/src/lib/types/index.types.ts` | `FieldEntry`, `FieldConfig` interfaces |
