# Index Mapping — Design Document

Per-index configuration that controls how raw source data is shaped before it reaches Typesense.
Editable from the **Mapping** tab in the index detail view (Configure → Mapping).

---

## Purpose

This document is the permanent reference for how ISP transforms raw connector data before it is
written to Typesense.  It applies to anyone setting up a new index, debugging unexpected field
values in search results, or adding a new PLP tab.

| Question | Root cause | Solution |
|---|---|---|
| Why does the Suggestions tab say "No category configured"? | `intent_label` is not set on the index | Set it in the Mapping tab |
| Why is a field named differently in Typesense than in the source API? | `field_map` is not configured | Add a field rename in the Mapping tab |
| Why does a field's value not match what ISP expects? | `value_map` is not configured | Add a value override in the Mapping tab |
| Why don't my documents have a `category` field? | `static_fields` is not configured | Add a static field value in the Mapping tab |

---

## Overview

Each ISP index has a Mapping configuration with four settings:

| Setting | Connector support | Purpose |
|---|---|---|
| **Intent label** | All | Which PLP tab this index powers (`device`, `accessory`, `smartwatch`, `tablet`) |
| **Field renames** | REST only | Rename a source field key before indexing (`tab → category`, `familyName → title`) |
| **Value overrides** | REST only | Replace a field's value after renaming (`category: accessories → accessory`) |
| **Static field values** | All | Stamp a constant key-value pair onto every document at index time (`category: account`) |

All four are applied at ingest and rebuild time, before documents are written to Typesense.
They have no effect at query time.

---

## Intent Label

The intent label is a free-text string stored on the `indexes` table.  It serves two purposes:

1. **Tab intent detection** — the `/v1/intent/tab` endpoint reads `intent_label` from all indexes to decide which PLP tab best matches a user's query.
2. **Suggestion scoping** — the suggestions system uses `intent_label` as the `category` value for catalog suggestions, so typeahead results are scoped to the right tab.

```sql
indexes.intent_label  TEXT  DEFAULT ''
```

### Supported values

| Value | PLP tab |
|---|---|
| `device` | Phones |
| `accessory` | Accessories |
| `smartwatch` | Smartwatches |
| `tablet` | Tablets |

Any free-text value is valid — the system is not hardcoded to these four.  Adding a new tab
only requires creating an index with a new `intent_label` and updating `INTENT_TO_TAB` in the PLP.

### Effect when empty

- Tab intent endpoint skips the index entirely (does not participate in the vote).
- Suggestions tab shows: *"No category configured. Set an intent label to enable scoped suggestions."*
- Catalog suggestions are seeded without a category — they appear globally in typeahead.

---

## Field Renames

Applies to **REST connectors only**.

Field renames map a source key to a target key before the document is indexed.
Stored as `field_map` in the connector config JSONB.

```json
{
  "field_map": {
    "tab":        "category",
    "familyName": "title",
    "legacyId":   ""
  }
}
```

Rules:
- Key = field name as it arrives from the source API.
- Value = field name to store in ISP. Set to `""` to drop the field entirely.
- Fields not listed in `field_map` pass through unchanged.
- Renames are applied before value overrides.

---

## Value Overrides

Applies to **REST connectors only**.

Value overrides replace a field's value after field renames have been applied.
Stored as `value_map` in the connector config JSONB.

```json
{
  "value_map": {
    "category": {
      "accessories": "accessory",
      "phones":      "device",
      "tablets":     "tablet"
    },
    "status": {
      "active": "in-stock"
    }
  }
}
```

Rules:
- Outer key = field name (after `field_map` renames).
- Inner key = raw value from the source.
- Inner value = replacement written to Typesense.
- Values not listed in the inner map pass through unchanged.

---

## Static Field Values

Applies to **all connector types** (REST, sitemap, gsheet).

Static field values stamp a constant key-value pair onto every document at index time.
Stored as `static_fields` in the connector config JSONB.

```json
{
  "static_fields": {
    "category": "account",
    "source":   "tmobile-support"
  }
}
```

Rules:
- Each entry adds a field with the given constant value to every document.
- Applied after extensions and after field renames / value overrides, so static values always win over source data.
- Fields land in the `metadata` JSON blob in Typesense (since they are not declared ISP schema fields).
- Survives rebuild — the mapping endpoint writes `static_fields` to the connector config, and the ingest/rebuild endpoints always preserve it when saving back.

Common uses:
- Stamp `category: account` on a GSheet index so every document is tagged for filtering or display.
- Stamp `source: tmobile-support` on a sitemap index to distinguish it from other sitemap indexes in mixed searches.

---

## Apply Order at Ingest

```
Raw source document
       │
       ▼  1. field_map  (REST only) — rename keys
       │     tab        →  category
       │     familyName →  title
       │     legacyId   →  (dropped)
       │
       ▼  2. value_map  (REST only) — rewrite values
       │     category: accessories  →  accessory
       │     category: phones       →  device
       │
       ▼  3. JS extensions — enrich / transform (all connectors)
       │
       ▼  4. static_fields (all connectors) — stamp constants
       │     category  →  account
       │
       ▼  5. ISP field extraction
       │     title, content, url, id → top-level
       │     all remaining fields    → metadata JSON blob
       │
       ▼
Typesense document
```

Steps 1–2 run inside `map_document()` in `rest_connector.py` (REST connectors only).
Steps 3–5 run in `_run_ingest` / `_run_rebuild` in `indexes.py` for all connector types.

---

## Rebuild Requirement

| Change | Rebuild needed? |
|---|---|
| Update `intent_label` | No — used at query time and suggestion seeding only |
| Update `field_map` | Yes — existing documents have already been indexed with the old keys |
| Update `value_map` | Yes — existing documents have already been stored with the old values |
| Update `static_fields` | Yes — existing documents do not have the new constant fields |

The UI shows a notice after saving field renames, value overrides, or static fields:
> "Saved. Rebuild this index to apply the mapping to existing documents."

---

## Data Model

### `indexes` table

```sql
intent_label  TEXT  DEFAULT ''
```

### Connector config JSONB (`connectors` column on `indexes`)

All three mapping keys are optional and present on all connector types.
`field_map` and `value_map` are only acted on for REST connectors.

```json
{
  "type":          "gsheet",
  "spreadsheet_id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
  "sheet_name":    "Sheet1",
  "field_map":     {},
  "value_map":     {},
  "static_fields": { "category": "account" }
}
```

```json
{
  "type":       "rest",
  "url":        "https://api.example.com/products",
  "method":     "GET",
  "field_map":  { "tab": "category" },
  "value_map":  { "category": { "accessories": "accessory" } },
  "static_fields": {}
}
```

---

## API

### `PUT /v1/indexes/{id}/intent-label`

Sets or clears the intent label for an index.  No rebuild required.

```bash
curl -X PUT http://localhost:8001/v1/indexes/22ffabfef4d1/intent-label \
  -H 'Content-Type: application/json' \
  -d '{"intent_label": "device"}'
```

Response: full `IndexMeta` with updated `intent_label`.

---

### `PUT /v1/indexes/{id}/connectors/mapping`

Saves field renames, value overrides, and static field values onto the existing connector config.
Does not trigger a rebuild.  All three keys are required in the request body (send `{}` for any that are not used).

```bash
curl -X PUT http://localhost:8001/v1/indexes/847bf1f24c72/connectors/mapping \
  -H 'Content-Type: application/json' \
  -d '{
    "field_map":     { "tab": "category", "familyName": "title" },
    "value_map":     { "category": { "accessories": "accessory" } },
    "static_fields": { "source": "tmobile-support" }
  }'
```

Response: full `IndexMeta` with updated connector config.

---

## UI — Mapping Tab

The Mapping tab is inside the tabbed index detail view (Configure → Mapping).

```
┌──────────────────────────────────────────────────────┐
│ INTENT LABEL                                         │
│                                                      │
│ Intent label   [ account                ]            │
│ Scopes typeahead suggestions and tab intent          │
│ detection. Leave blank if this index does not        │
│ power a PLP tab.                                     │
│                                          [ Save ]    │
├──────────────────────────────────────────────────────┤
│ FIELD RENAMES                            REST only   │
│ Rename source fields before indexing.                │
│                                                      │
│  tab          →  category                       ✕   │
│  familyName   →  title                          ✕   │
│  + Add rename                                        │
│                                                      │
│ Rebuild required to apply to existing documents.     │
│                                          [ Save ]    │
├──────────────────────────────────────────────────────┤
│ VALUE OVERRIDES                          REST only   │
│ Rewrite field values after field renames.            │
│                                                      │
│  Field        Raw value      →  Stored as            │
│  category     accessories    →  accessory       ✕   │
│  category     phones         →  device          ✕   │
│  + Add override                                      │
│                                                      │
│ Rebuild required to apply to existing documents.     │
│                                          [ Save ]    │
├──────────────────────────────────────────────────────┤
│ STATIC FIELD VALUES                      All sources │
│ Stamp a constant value onto every document.          │
│                                                      │
│  category     →  account                        ✕   │
│  + Add field                                         │
│                                                      │
│ Rebuild required to apply to existing documents.     │
│                                          [ Save ]    │
└──────────────────────────────────────────────────────┘
```

Each section saves independently via its own Save button.

---

## File Reference

| File | Role |
|---|---|
| `backend/src/isp/models/index.py` | `RestConnectorConfig` (`field_map`, `value_map`, `static_fields`), `SitemapConnectorConfig` (`static_fields`), `GSheetConnectorConfig` (`static_fields`), `MappingConfig`, `IntentLabelUpdate` |
| `backend/src/isp/services/infra/rest_connector.py` | `map_document()` — applies `field_map` then `value_map` for REST |
| `backend/src/isp/routers/indexes.py` | `PUT /{id}/intent-label`, `PUT /{id}/connectors/mapping`; `static_fields` stamping in `_run_ingest` and `_run_rebuild` for all connector types |
| `frontend/src/components/sources/MappingTab.svelte` | Mapping tab — intent label, field renames, value overrides, static field values |
| `frontend/src/components/sources/IndexDetail.svelte` | Tab router — includes `mapping` tab, renders `<MappingTab>` |
| `frontend/src/lib/services/indexes/IndexService.ts` | `saveIntentLabel()`, `saveMapping()` |
| `frontend/src/lib/types/index.types.ts` | `MappingConfig` interface |
