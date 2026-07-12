# REST Connector Pre-Processor

## What it is

A JavaScript hook that runs on the **full raw API response** before `result_path` extraction splits it into individual documents. It has full access to the entire response — sibling arrays, top-level wrappers, nested structures — before any per-item logic runs.

```
HTTP fetch
    ↓
pre_process(response)      ← sees entire response, can reshape freely
    ↓
result_path extraction     ← navigates to the item array
    ↓
[item1, item2, …]
    ↓
transform(item)            ← existing per-item extension
    ↓
Typesense upsert
```

## Why it exists

The per-item extension pipeline cannot see outside its own document. If the API response contains sibling arrays, top-level metadata, or a non-standard structure that needs reshaping before `result_path` can navigate it, a per-item extension cannot fix that. The pre-processor fills this gap.

**Typical use cases:**

| Problem | Solution |
|---|---|
| Items array is nested inside multiple wrapper keys | Navigate and return just the array |
| Two sibling arrays need to be joined (e.g. `ratePlans` + `ratePlanFeatures`) | Merge in pre_process, return enriched items |
| API wraps the real payload in a `data.result.items` chain | Flatten the chain |
| Response is a single object, not a list | Wrap it: `return { items: [response] }` then set `result_path = items` |

## JS contract

```js
function pre_process(response) {
  // response — the full parsed JSON from the API
  // must return an object (the transformed response)
  return response;
}
```

- Runs in a V8 isolate (PyMiniRacer) — no network, no filesystem, pure JS
- Must return a plain object; returning a non-object falls back to the original response
- Runtime errors fall back to the original response and log an error — the rebuild continues

## Two ways to configure

### Option 1: File-based (recommended)

Create a `.js` file in `backend/extensions/` with `// @type pre_processor` in the header:

```js
// @name Plans Pre-Processor
// @description Joins ratePlanFeatures into each ratePlanOffer by billingSoc
// @type pre_processor
// @assign none

function pre_process(response) {
  var featureMap = {};
  (response.ratePlanFeatures || []).forEach(function(rpf) {
    featureMap[rpf.soc] = rpf.featuresByRank || [];
  });

  (response.ratePlans || []).forEach(function(rp) {
    (rp.ratePlanOffers || []).forEach(function(offer) {
      offer.rankedFeatures = featureMap[offer.billingSoc] || [];
    });
  });

  return response;
}
```

On next restart the file is seeded into the extensions library with `type = 'pre_processor'`. Go to the index's **Extensions** tab and assign it — it runs before `result_path` extraction, the per-item `transform` extension runs after.

**Header annotations:**

| Annotation | Effect |
|---|---|
| `// @type pre_processor` | Marks as pre-processor; function must be `pre_process(response)` |
| `// @assign none` | Skips auto-assignment at startup (pre-processors are always assigned manually) |
| `// @name …` | Display name in the Extensions library |
| `// @description …` | One-line description |

> Pre-processor type extensions are never auto-assigned at startup even without `@assign none`. The annotation is optional but makes intent explicit.

### Option 2: Inline textarea

Rebuild panel → REST connector form → **▸ Pre-processor** (collapsed by default). Paste the JS there. It is saved as `pre_processor` on the connector config and persisted alongside the URL and headers.

Use the inline option for quick experiments; use file-based for anything that goes to production or needs to be shared across indexes.

## Execution order (when both are present)

If an index has both an inline `pre_processor` string on the connector config AND one or more file-based pre-processor extensions assigned, they run in this order:

1. Inline `pre_processor` string (connector config)
2. File-based pre-processor extensions, in `run_order` sequence

The output of each stage becomes the input of the next.

## Extension type field

The `extensions` table has a `type` column (`'transform'` or `'pre_processor'`):

- `transform` — runs per item via `function transform(doc) { … return doc; }` (default)
- `pre_processor` — runs once on the raw response via `function pre_process(response) { … return response; }`

The type is set when the extension is created and determines which execution stage it participates in. An extension assigned to an index will only run at the correct stage regardless of `run_order`.

## Storage

- **File-based:** `type` column on the `extensions` table, set at seed time from `// @type` annotation
- **Inline:** `pre_processor: str` field on `RestConnectorConfig` (models/index.py), stored in the `connectors` JSONB column on the `indexes` table

Empty string / no assignment = disabled (no V8 invocation, zero overhead).

## Execution path

```
rest_connector.fetch_raw_items(cfg, pre_processor_exts)
    → _do_request(cfg)                       HTTP fetch
    → run_pre_processor(cfg.pre_processor)   inline string (if set)
    → apply_pre_processor_extensions(data, pre_processor_exts)  file-based (if assigned)
    → _extract_path(data, result_path)       item array navigation
```

Both `fetch_raw_items` (catalog write-back) and `fetch_and_parse` (ISP-managed rebuild) follow this path, so all rebuild flows benefit.

## Example: join ratePlanFeatures into each offer

```js
// @name Plans Pre-Processor
// @description Joins ratePlanFeatures into each offer by billingSoc before per-item transform runs
// @type pre_processor

function pre_process(response) {
  // Build lookup: billingSoc → featuresByRank[]
  var featureMap = {};
  (response.ratePlanFeatures || []).forEach(function(rpf) {
    featureMap[rpf.soc] = rpf.featuresByRank || [];
  });

  // Inject ranked features into every offer
  (response.ratePlans || []).forEach(function(rp) {
    (rp.ratePlanOffers || []).forEach(function(offer) {
      offer.rankedFeatures = featureMap[offer.billingSoc] || [];
    });
  });

  return response;
}
```

Then set `result_path = ratePlans.ratePlanOffers`. The per-item transform extension will find `doc.rankedFeatures` already populated.
