# ISP Setup Agent — AI-Powered Index Creation

## Overview

The Setup Agent creates a fully configured ISP search index from a JSON file. It inspects your data, generates three JavaScript extensions (normalizer, chatContent, suggestions), configures Typesense field types, and runs the first ingest — all in one step.

Three analysis modes control how field detection works:

| Mode | How it works | API calls |
|---|---|---|
| `auto` | Code heuristics only (`code_analyst`) | 0 — instant and free |
| `hybrid` | Code first; Claude fallback if confidence < 65% | 0 or 1 |
| `ai` | Claude only (`claude_analyst`) | 1 (+ up to 2 self-correction retries) |

**Entry points:**
- `GET  /v1/agent/data-files` — list JSON files available in `/app/data/`
- `POST /v1/agent/preview` — dry-run, no index created, returns normalised sample doc
- `POST /v1/agent/setup` — full pipeline, streams SSE progress events

**UI entry point:** Sidebar → ✦ Agent (above Extensions)

---

## UI — AgentPanel

`AgentPanel.svelte` is a tab shell with two tabs:

| Tab | Component | Purpose |
|---|---|---|
| Setup Content | `AgentSetup.svelte` | Generic JSON setup — configurable root key, full mode selector, feedback loop |
| Shopify | `ShopifySetup.svelte` | Shopify product exports — file dropdown, pre-fills `root_key=products`, defaults to `auto` |

### ShopifySetup flow

1. On mount: `GET /v1/agent/data-files` → populates file dropdown with all `.json` files in `/app/data/`
2. User picks file + index name → **Preview schema** or **Build Index**
3. Preview shows normalised sample doc + **Build Index →** confirm button
4. Build streams SSE progress same as AgentSetup

---

## End-to-End Code Flow

```
AgentSetup.svelte / ShopifySetup.svelte
  │
  ├─ GET /v1/agent/data-files
  │     → lists *.json in /app/data/, returns { files: [...] }
  │
  ├─ POST /v1/agent/preview ─────────────────────────────────────────────────┐
  │     agent_setup.py :: agent_preview()                                     │
  │       1. json_sampler.load()          → sample, root_key, total           │
  │       2. schema_extractor.extract()   → schema dict                       │
  │       3. _resolve_artifacts()         → artifacts  (see mode routing)     │
  │       4. Run normalizer_js on sample[0] via extension_runner._run_sync()  │
  │       5. _flatten_objects() on preview result                             │
  │       Return: { artifacts, preview_doc, schema, total_items }             │
  │                                                                            │
  └─ POST /v1/agent/setup ───────────────────────────────────────────────────┘
        agent_setup.py :: agent_setup()  →  StreamingResponse (SSE)
          1. json_sampler.load()
          2. schema_extractor.extract()
          3. _resolve_artifacts()        ← mode-aware analyst routing
          4. setup_executor.run()        ← yields SSE events
               ├─ index_service.create_index()
               ├─ ts.create_collection()
               ├─ extension_service.upsert_extension_by_name()  × 3
               ├─ extension_service.assign_extension()          × 3
               ├─ index_service.add_connector()
               ├─ index_service.update_index()  (field_config)
               └─ _run_rebuild_sync()
                    ├─ json_file_connector.fetch_raw_items()
                    ├─ apply_extensions()   (runs the 3 JS extensions)
                    ├─ _ensure_isp_schema() (packs extra fields into metadata)
                    ├─ _repair_doc()        (fixes any bad JS output)
                    ├─ embedding_service.embed_texts()
                    ├─ ts.upsert_documents()
                    └─ suggestion_service.seed_suggestions_from_index()
```

---

## Mode Routing — `_resolve_artifacts()`

`agent_setup.py` routes to the correct analyst based on the `mode` field:

```python
async def _resolve_artifacts(sample, schema, index_name, api_key, feedback, mode):

    if mode == "auto":
        artifacts = code_analyst.analyse(sample, schema, index_name)
        # confidence score included in artifacts["_confidence"]
        return artifacts, f"Auto-detected — {n} fields mapped (confidence {conf}%)"

    if mode == "ai":
        artifacts = await claude_analyst.analyse_with_correction(
            sample, schema, index_name, api_key, feedback=feedback)
        return artifacts, f"AI analysis complete — {n} fields mapped"

    # hybrid: code first; Claude fallback if confidence < HYBRID_THRESHOLD (65)
    code_artifacts = code_analyst.analyse(sample, schema, index_name)
    if code_artifacts["_confidence"] >= HYBRID_THRESHOLD:
        return code_artifacts, f"Auto-detected (confidence {conf}%, no AI needed)"

    # Low confidence — enrich Claude's prompt with code findings as context
    code_context = confidence_summary(detect_roles(sample, schema))
    artifacts = await claude_analyst.analyse_with_correction(
        sample, schema, index_name, api_key,
        feedback=feedback + "\n\n" + code_context)
    return artifacts, f"Hybrid — code confidence {conf}%, AI used"
```

Both analysts return the same artifact shape:

```json
{
  "normalizer_js":    "function transform(doc) { ... }",
  "chat_content_js":  "function transform(doc) { ... }",
  "suggestion_js":    "function transform(doc) { ... }",
  "field_config":     [{ "name": "title", "type": "string", "searchable": true, "facet": false }, ...],
  "intent_label":     "shoe",
  "id_field":         "id",
  "title_field":      "title",
  "content_field":    "content",
  "url_field":        "url",
  "_confidence":      86,   // code_analyst only
  "_method":          "code" | "hybrid-code" | "hybrid-ai" | "ai"
}
```

---

## Step 1 — `json_sampler.load()` (`agent/json_sampler.py`)

Loads the JSON file and returns a **representative sample** of up to 5 items.

```python
async def load(path, sample_size=5, root_key="") -> (sample, detected_key, total):
```

1. **Strips `file://` prefixes** — browsers sometimes prepend `file:///`.
2. **Supports HTTP URLs** — fetches via `httpx` if path starts with `http://`.
3. **Auto-detects root key** — finds the first key whose value is a non-empty list of dicts.
4. **Evenly-spaced sample** — takes first, last, and evenly distributed middle items to give the analyst variety across large catalogs.

---

## Step 2 — `schema_extractor.extract()` (`agent/schema_extractor.py`)

Builds a **flat field schema** by walking all sample items and merging their structure.

```python
def extract(sample) -> dict[str, str]:
    # Returns: { "title": "string", "variants[].price": "float", "images": "array[object] (5 items)", ... }
```

- Recursively walks nested dicts up to depth 3, using dot / bracket notation: `variants[].price`
- Merges fields across all sample items — handles sparse optional fields
- `null` type is overwritten if a non-null value is seen in another sample item

---

## Step 3a — `code_analyst.analyse()` (`agent/code_analyst.py`)

Zero-token heuristic analyst. Works well for standard product catalogs (Shopify, WooCommerce, etc.). The `auto` and `hybrid` modes use this first.

### Role detection — `_detect_roles(sample, schema)`

Three-pass detection:

1. **Name match** — field names checked against pattern sets:
   - `_TITLE_NAMES = {"title", "name", "product_name", ...}`
   - `_CONTENT_NAMES = {"body_html", "description", "content", ...}`
   - `_URL_NAMES = {"handle", "url", "slug", "permalink", ...}`
   - `_IMAGE_NAMES = {"images", "image", "photo", "thumbnail", ...}`
   - `_PRICE_NAMES = {"price", "cost", "amount", ...}`
   - `_ID_NAMES = {"id", "product_id", "sku", ...}`

2. **Nested array patterns** — schema keys like `variants[].price` or `images[].src` are used to detect roles from nested fields.

3. **Value fallbacks** — if name matching fails, inspects actual values (looks for URL-shaped strings, numeric prices, image extensions, etc.)

### Confidence scoring — `_calc_confidence(roles)`

```python
weights = { "id": 15, "title": 25, "content": 20, "url": 15, "image": 15, "price": 10 }
confidence = sum(weights[role] for role in detected_roles)
```

Standard Shopify export: confidence 86% (all 6 roles detected).

### Normalizer JS generation — `_build_normalizer_js(roles, sample)`

Generates ES5-compatible JS from Python templates:

```js
function transform(doc) {
  doc.title   = doc.title || "";
  var _raw    = doc.body_html || "";
  doc.content = _raw.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
  delete doc.body_html;
  doc.url     = doc.handle ? ("/products/" + doc.handle) : (doc.url || "");
  doc.image   = (doc.images && doc.images.length && doc.images[0]) ? (doc.images[0].src || "") : "";
  doc.price   = parseFloat((doc.variants && doc.variants.length) ? (doc.variants[0].price || 0) : 0);
  if (doc.available !== undefined) { doc.in_stock = !!doc.available; }
  // Array flattening (generated by _append_array_handling):
  doc.tags     = (doc.tags || []).join(", ");
  doc.variants = (doc.variants || []).map(function(x){ return x.title || ""; }).filter(Boolean).join(", ");
  doc.images   = (doc.images || []).map(function(x){ return x.src || ""; }).filter(Boolean).join(", ");
  doc.options  = (doc.options || []).map(function(x){ return x.name || ""; }).filter(Boolean).join(", ");
  return doc;
}
```

**Important:** `_append_array_handling` always flattens every array-of-dicts field to a string — including fields that were already used as a scalar source (price from `variants[0].price`, image URL from `images[0].src`). Without this, those fields would remain as list-of-dicts and get packed into the `metadata` blob as nested objects, causing `[object Object]` display in the UI.

### Field config — `_build_field_config(sample, schema, roles)`

Uses cardinality analysis to set `searchable` and `facet` flags:
- High-cardinality fields (many unique values) → `searchable: true`, `facet: false`
- Low-cardinality fields (few unique values) → `facet: true` (e.g. `product_type`, `vendor`)
- Numeric / bool fields → `facet: true`

---

## Step 3b — `claude_analyst.analyse_with_correction()` (`agent/claude_analyst.py`)

Used in `ai` mode, or as the `hybrid` fallback when code confidence < 65%.

### `analyse()` — the Claude call

Sends schema + sample items to Claude Opus with a structured prompt instructing ES5-only JS output. Returns raw JSON with `normalizer_js`, `chat_content_js`, `suggestion_js`, `field_config`, `intent_label`, and field name mappings.

### Self-correction loop

After `analyse()` returns, the normalizer is **actually run** against `sample[0]` using the same Duktape runner used in production:

```python
ext = [{"id": "selftest", "code": normalizer_code, "type": "transform", "enabled": True}]
result = await asyncio.to_thread(_run_sync, [dict(sample[0])], ext)
bad = _find_bad_fields(result[0])
```

`_find_bad_fields` flags:
- `isinstance(val, dict)` — still a nested object
- `isinstance(val, list) and isinstance(val[0], dict)` — still array of objects
- `"[object Object]" in val` — JS did `.join()` without `.map()` first

If bad fields are found, a correction note is generated and `analyse()` is re-called with the feedback. Up to **2 retries**. The SSE stream reports `"(auto-corrected 1x)"` if any retries happened.

---

## Step 4 — `setup_executor.run()` (`agent/setup_executor.py`)

An **async generator** that executes all ISP setup steps and yields SSE-compatible event dicts.

### Extensions saved

| Extension name | JS function | Run order |
|---|---|---|
| `agent_normalizer_<id>` | `transform(doc)` — flattens nested fields, maps roles | 1 |
| `agent_chat_content_<id>` | `transform(doc)` — builds `doc.chatContent` as labelled string | 2 |
| `agent_suggestions_<id>` | `extractSuggestions(doc)` + `transform(doc)` | 3 |

**Note on suggestion extension:** Claude's `suggestion_js` output is ignored. A hardcoded Python template is used to guarantee the correct function signature — Claude historically generates non-standard patterns (`emit()` etc.) here.

### `_run_rebuild_sync()` — ingest, embed, index

```python
raw_items = await json_file_connector.fetch_raw_items(jf_cfg)

if transform_exts:
    docs = await apply_extensions(list(raw_items), transform_exts)
    docs = _ensure_isp_schema(docs)           # packs extra fields into metadata blob
    docs = [_repair_doc(docs[i], raw_items[i]) for i in range(len(docs))]
else:
    docs = _ensure_isp_schema(list(raw_items))
    docs = [_flatten_objects(d) for d in docs]
```

---

## `_repair_doc` — Post-Extension Safety Net

Runs after every `apply_extensions()` call on json_file indexes:

```python
def _repair_doc(processed: dict, raw: dict) -> dict:
    for key, val in list(processed.items()):

        # Case 1: extension left a nested dict/list-of-dicts untouched
        if isinstance(val, dict) or (isinstance(val, list) and val and isinstance(val[0], dict)):
            doc.update(_flatten_objects({key: val}))

        # Case 2: JS called .join() on array of objects without .map()
        # → produced "[object Object], [object Object], ..."
        elif isinstance(val, str) and re.search(r'\[object Object\]', val):
            raw_val = raw.get(key)
            if raw_val is not None:
                doc.update(_flatten_objects({key: raw_val}))   # re-flatten from original
            else:
                doc[key] = ""
```

`_flatten_objects()` converts a nested value to a string using priority keys (`title`, `name`, `src`, `url`, `href`, `value`, `label`, `text`). Special case: `images` array backfills `doc.image` with `images[0].src` if `image` is not already set.

**Scope:** only called in json_file rebuild paths. REST, sitemap, and GSheet rebuilds are unaffected.

---

## `_ensure_isp_schema()` and the `metadata` field

ISP Typesense documents have a fixed top-level schema (`id`, `title`, `content`, `url`, `vec`, `indexed_at`, `metadata`, and a small set of typed fields). Any field not in this schema is packed into the `metadata` JSONB string:

```python
combined_meta = {k: v for k, v in doc.items() if k not in _ISP_FIELDS}
out["metadata"] = json.dumps(combined_meta)
```

For Shopify indexes, `metadata` contains: `handle`, `vendor`, `product_type`, `tags`, `variants`, `images`, `options`, `price`, `in_stock`, `chatContent`, etc.

**Why variants must be flattened before `_ensure_isp_schema` runs:** if `variants` is still a list-of-dicts when packed into `metadata`, it serialises to a JSON array of objects. The ISP document browser renders these as `[object Object]` when displaying the metadata contents. The `code_analyst` normalizer_js and `_repair_doc` both ensure variants is a flat string before `_ensure_isp_schema` is called.

---

## Suggestion Seeding

After `ts.upsert_documents()` completes, `suggestion_service.seed_suggestions_from_index(index_id)` scans all indexed documents, calls `extractSuggestions(doc)` on each via the suggestions extension, and inserts `{query, category}` pairs into `isp_suggestions`. These power the autocomplete search bar.

---

## File Locations

| File | Role |
|---|---|
| `backend/src/isp/routers/agent_setup.py` | FastAPI router — data-files, preview, setup endpoints; mode routing via `_resolve_artifacts` |
| `backend/src/isp/services/infra/agent/json_sampler.py` | Load JSON file, detect root key, even sample |
| `backend/src/isp/services/infra/agent/schema_extractor.py` | Walk sample items → flat schema dict |
| `backend/src/isp/services/infra/agent/code_analyst.py` | Zero-token heuristic analyst — role detection, confidence scoring, JS generation |
| `backend/src/isp/services/infra/agent/claude_analyst.py` | Claude Opus call + self-correction loop |
| `backend/src/isp/services/infra/agent/setup_executor.py` | SSE generator — creates index, extensions, runs rebuild; `_repair_doc`, `_flatten_objects` |
| `backend/src/isp/services/infra/json_file_connector.py` | Reads JSON file for the connector |
| `frontend/src/components/settings/AgentPanel.svelte` | Tab shell — Setup Content + Shopify tabs |
| `frontend/src/components/settings/AgentSetup.svelte` | Generic JSON setup form with mode selector, SSE terminal, feedback loop |
| `frontend/src/components/settings/ShopifySetup.svelte` | Shopify-specific form — file dropdown, auto mode, confirm-after-preview |

---

## Auth

`/v1/agent` is in `_PUBLIC_API_PREFIXES` in `auth/middleware.py` — it bypasses ISP JWT authentication. The agent authenticates with the Anthropic API using the key the user provides in the form, or falls back to the key stored in Settings → Chat Model.

---

## JSON File Placement

The API container mounts `./data:/app/data:ro` (read-only). Drop your JSON file in the project `data/` folder:

```
intelligent-search-platform/
  data/
    allbirds.json   →  /app/data/allbirds.json
    aloyoga.json    →  /app/data/aloyoga.json
```

The Shopify tab automatically lists all `.json` files found here via `GET /v1/agent/data-files`.

---

## Rebuilding an Agent Index

Agent-created indexes use the `json_file` connector. The Rebuild panel shows the source path and a **Run Rebuild** button. Clicking it re-runs the full ingest + extension + repair + embed pipeline without calling the analyst again.

To change the normalizer after setup: go to Extensions, find `agent_normalizer_<id>`, edit the JS, then Run Rebuild.

---

## Known Constraints

- **ES5 only** — the Duktape JS engine does not support arrow functions, `const`/`let`, template literals, or spread syntax. All generated JS uses `var`, `function() {}`, and string concatenation.
- **Shopify tab is Shopify-specific** — `root_key` is hardcoded to `products`. For other JSON structures use the Setup Content tab.
- **`docker cp` required for backend code changes** — the API container does not volume-mount `/app/src/`, so Python file changes require `docker cp <file> isp-api:/app/src/<path>` followed by a container restart to reload.
- **Frontend requires a rebuild** — `npm run build` in `frontend/` followed by `docker cp frontend/dist/. isp-ui:/usr/share/nginx/html/` to deploy changes to the nginx container.
