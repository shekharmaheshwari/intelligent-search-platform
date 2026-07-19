# ISP Setup Agent — AI-Powered Index Creation

## Overview

The Setup Agent creates a fully configured ISP search index from a JSON file using Claude AI. It inspects your data, generates three JavaScript extensions (normalizer, chatContent, suggestions), configures Typesense field types, and runs the first ingest — all in one step.

**Entry points:**
- `POST /v1/agent/preview` — dry-run, no index created, returns normalised sample doc
- `POST /v1/agent/setup` — full pipeline, streams SSE progress events

---

## End-to-End Code Flow

```
AgentSetup.svelte
  │
  ├─ POST /v1/agent/preview ──────────────────────────────────────────────────┐
  │     agent_setup.py :: agent_preview()                                      │
  │       1. json_sampler.load()          → sample, root_key, total            │
  │       2. schema_extractor.extract()   → schema dict                        │
  │       3. claude_analyst.analyse_with_correction() → artifacts              │
  │       4. Run normalizer_js on sample[0] via extension_runner._run_sync()  │
  │       5. _repair_doc() on the preview result                               │
  │       Return: { artifacts, preview_doc, schema, total_items }              │
  │                                                                             │
  └─ POST /v1/agent/setup ────────────────────────────────────────────────────┘
        agent_setup.py :: agent_setup()  →  StreamingResponse (SSE)
          1. json_sampler.load()
          2. schema_extractor.extract()
          3. claude_analyst.analyse_with_correction()
          4. setup_executor.run()   ← yields SSE events
               ├─ index_service.create_index()
               ├─ ts.create_collection()
               ├─ extension_service.upsert_extension_by_name()  × 3
               ├─ extension_service.assign_extension()          × 3
               ├─ index_service.add_connector()
               ├─ index_service.update_index()  (field_config)
               └─ _run_rebuild_sync()
                    ├─ json_file_connector.fetch_raw_items()
                    ├─ apply_extensions()   (runs the 3 JS extensions)
                    ├─ _repair_doc()        (fixes bad JS output)
                    ├─ embedding_service.embed_texts()
                    ├─ ts.upsert_documents()
                    └─ suggestion_service.seed_suggestions_from_index()
```

---

## Step 1 — `json_sampler.load()` (`agent/json_sampler.py`)

Loads the JSON file and returns a **representative sample** of up to 5 items.

```python
async def load(path, sample_size=5, root_key="") -> (sample, detected_key, total):
```

**What it does:**

1. **Strips `file://` prefixes** — browsers sometimes prepend `file:///` when a path is dragged in. Stripped before opening.

2. **Supports HTTP URLs** — if `path` starts with `http://` or `https://`, fetches via `httpx` instead of reading from disk.

3. **Auto-detects root key** — if the JSON is `{ "products": [...] }`, it finds the first key whose value is a non-empty list of dicts. No need to specify `root_key` manually.

4. **Evenly-spaced sample** — for a 500-item catalog, rather than taking the first 5 (which might all be the same category), it spaces indices evenly:
   ```python
   step = total // (sample_size - 1)
   indices = sorted({0, total - 1} | {i * step for i in range(1, sample_size - 1)})
   sample = [items[i] for i in indices[:sample_size]]
   ```
   This gives Claude variety across large catalogs — first item, last item, and 3 distributed in between.

---

## Step 2 — `schema_extractor.extract()` (`agent/schema_extractor.py`)

Builds a **flat field schema** by walking all sample items and merging their structure.

```python
def extract(sample: list[dict]) -> dict[str, str]:
    # Returns: { "title": "string", "variants[].price": "float", "images": "array[object] (5 items)", ... }
```

**What it does:**

- `_flatten()` recursively walks nested dicts up to **depth 3**, using dot notation for nested keys: `category.name`, `variants[].price`
- For arrays of objects it shows `array[object] (N items)` and also flattens the first element's structure: `variants[].title`, `variants[].price`, etc.
- `_infer_type()` maps Python types to readable labels: `bool`, `int`, `float`, `string`, `array[string]`, `object`, `null`
- Fields from ALL sample items are merged — if a field is missing in some items it still appears (handles sparse optional fields)
- `null` type is overwritten if a non-null value is seen in another sample item

**Why this matters:** Claude sees a concise structural map rather than the full raw data payload. This keeps the prompt small while giving Claude everything it needs to reason about field types and nesting depth.

---

## Step 3 — `claude_analyst.analyse_with_correction()` (`agent/claude_analyst.py`)

Calls Claude Opus with the schema + sample, then **self-tests and auto-corrects** the generated normalizer.

### 3a. `analyse()` — the Claude call

```python
async def analyse(sample, schema, index_name, api_key, feedback="") -> dict:
```

**Prompt structure sent to Claude:**

```
[SYSTEM]  _SYSTEM prompt (see below)

[USER]
Index name: allbirds

Schema (field path → type):
{
  "title": "string",
  "variants": "array[object] (13 items)",
  "variants[].title": "string",
  "variants[].price": "string",
  "images": "array[object] (5 items)",
  "images[].src": "string",
  ...
}

Sample items (5 items):
[ { "title": "Women's Wool Runner", "variants": [...], ... }, ... ]

## Feedback from previous attempt   ← only present on correction retries
  - 'variants' became "[object Object]" — use .map() ...
```

**The system prompt (`_SYSTEM`) instructs Claude to:**
- Use **ES5 only** (Duktape JS engine — no arrow functions, no `const`/`let`, no template literals)
- `normalizer_js`: must produce `function transform(doc)` that flattens all nested fields
- `chat_content_js`: must produce `function transform(doc)` that builds `doc.chatContent` as a labelled string
- `suggestion_js`: must define BOTH `extractSuggestions(doc)` (returns `[{query, category}]`) AND `function transform(doc) { return doc; }`
- `field_config`: JSON array of `{ name, type, searchable, facet }` objects — no `vec`, `id`, `indexed_at` (ISP manages those)

**Claude returns raw JSON** (no markdown). If it wraps in code fences, the analyst strips them:
```python
if raw.startswith("```"):
    raw = raw.split("```")[1]
    if raw.startswith("json"):
        raw = raw[4:]
```

### 3b. `analyse_with_correction()` — the self-test loop

After `analyse()` returns, the generated `normalizer_js` is **actually run** against `sample[0]` using the same JS extension runner used in production:

```python
ext = [{"id": "selftest", "code": normalizer_code, "type": "transform", "enabled": True}]
result = await asyncio.to_thread(_run_sync, [dict(sample[0])], ext)
test_doc = result[0]
```

`_find_bad_fields(test_doc)` then checks every field in the output:

```python
def _find_bad_fields(doc):
    # Flags:
    # • isinstance(val, dict)                        → still a nested object
    # • isinstance(val, list) and isinstance(val[0], dict) → still array of objects
    # • isinstance(val, str) and "[object Object]" in val  → JS did bad .join()
```

If bad fields are found, an auto-correction note is generated with specific field names and fix instructions, appended to `feedback`, and `analyse()` is called again. This repeats up to **2 times**.

The final artifacts include `_auto_corrections: [...]` — a list of correction notes applied. The SSE stream reports `"(auto-corrected 1x)"` in the analyse step message if any retries happened.

---

## Step 4 — `setup_executor.run()` (`agent/setup_executor.py`)

An **async generator** that executes all ISP setup steps and yields SSE-compatible event dicts. The router streams each yielded dict directly to the browser.

```python
async def run(artifacts, index_name, json_path, root_key, total_items) -> AsyncIterator[dict]:
```

Each `yield` has the shape: `{ "step": str, "status": "running"|"ok"|"error", "message": str, "ts": int, ...extra }`

### Step 4.1 — Create index + Typesense collection

```python
meta = await index_service.create_index(
    name=index_name,
    description=f"Created by ISP Setup Agent from {json_path}",
    intent_label=artifacts["intent_label"],   # e.g. "shoe", "plant", "device"
)
ts.create_collection(meta.id, vec_dim=get_vec_dim())
```

`index_service.create_index()` writes a row to `isp_indexes` in PostgreSQL and returns an `IndexMeta` with an auto-generated UUID `id`. `ts.create_collection()` creates the Typesense collection `isp_<id>` with the current embedding model's vector dimension.

### Step 4.2 — Save 3 extensions to DB

```python
ext_normalizer = await extension_service.upsert_extension_by_name(
    name=f"agent_normalizer_{index_id}",
    description=f"Auto-generated normalizer for {index_name}",
    code=artifacts["normalizer_js"],
    ext_type="transform",
)
ext_chat = await extension_service.upsert_extension_by_name(
    name=f"agent_chat_content_{index_id}",
    code=artifacts["chat_content_js"],
    ext_type="transform",
)
ext_suggest = await extension_service.upsert_extension_by_name(
    name=f"agent_suggestions_{index_id}",
    code=_build_suggestion_js(artifacts),   # ← hardcoded template, NOT Claude's suggestion_js
    ext_type="transform",
)
```

`upsert_extension_by_name()` inserts or updates in `isp_extensions`. Extensions are named with the index ID so multiple agent-created indexes don't collide.

**Note on `_build_suggestion_js`:** Claude's `suggestion_js` output is ignored for the suggestions extension. Instead a hardcoded Python template is used to guarantee the `extractSuggestions(doc)` function signature is correct — Claude historically generates `emit()` or other non-standard patterns here.

```python
def _build_suggestion_js(artifacts):
    intent_label = artifacts.get("intent_label", "item")
    return f"""
function extractSuggestions(doc) {{
  var results = [];
  var title = doc.title || "";
  var category = doc.intent_label || "{intent_label}";
  if (title) {{ results.push({{query: title.toLowerCase(), category: category}}); }}
  var brand = doc.brand || "";
  var cat = doc.category_name || doc.category || "";
  if (brand && cat) {{ results.push({{query: (brand+" "+cat).toLowerCase(), category: category}}); }}
  return results;
}}
function transform(doc) {{ return doc; }}
""".strip()
```

### Step 4.3 — Assign extensions to index

```python
await extension_service.assign_extension(index_id, ext_normalizer["id"], run_order=1)
await extension_service.assign_extension(index_id, ext_chat["id"],       run_order=2)
await extension_service.assign_extension(index_id, ext_suggest["id"],    run_order=3)
```

Writes to `isp_index_extensions` join table. `run_order` controls execution sequence during rebuild — normalizer always runs first so downstream extensions see clean flat fields.

### Step 4.4 — Configure json_file connector

```python
connector_cfg = {
    "type":          "json_file",
    "path":          json_path,           # e.g. /app/data/allbirds.json
    "root_key":      root_key,            # e.g. "products"
    "id_field":      artifacts["id_field"],
    "title_field":   artifacts["title_field"],
    "content_field": artifacts["content_field"],
    "url_field":     artifacts["url_field"],
}
await index_service.add_connector(index_id, connector_cfg)
```

Persisted in the `connectors` JSONB column of `isp_indexes`. The Rebuild panel reads this to pre-populate the UI and knows to show the JSON file path instead of a REST form.

### Step 4.5 — Apply field_config

```python
field_config = artifacts.get("field_config", [])
# e.g. [{"name": "title", "type": "string", "searchable": true, "facet": false}, ...]
meta.field_config = field_config
await index_service.update_index(meta)
```

Stored in the `field_config` JSONB column. Used by the Fields tab in the index detail view and by Typesense schema management.

### Step 4.6 — `_run_rebuild_sync()` — ingest, embed, index

Because we're inside an SSE generator that must yield events, the rebuild runs **synchronously** (awaited inline) rather than as a FastAPI background task. This lets the executor yield the `rebuild` done event only after ingest is complete.

```python
# 1. Fetch all raw items from JSON file
raw_items = await json_file_connector.fetch_raw_items(jf_cfg)

# 2. Run the 3 JS extensions on every item
docs = await apply_extensions(list(raw_items), transform_exts)
docs = _ensure_isp_schema(docs)

# 3. Repair any fields the extension botched
docs = [_repair_doc(docs[i], raw_items[i]) for i in range(len(docs))]

# 4. Build embed text and call embedding model
raw_texts = [_build_embed_text(d) for d in docs]
vecs = await embedding_service.embed_texts([raw_texts[i] for i in embed_idxs])
for pos, idx in enumerate(embed_idxs):
    docs[idx]["vec"] = vecs[pos]

# 5. Drop + recreate Typesense collection, upsert all docs
ts.drop_collection(index_id)
ts.create_collection(index_id, vec_dim=rebuild_dim)
ts.upsert_documents(index_id, docs)

# 6. Seed suggestions table from the now-indexed docs
await suggestion_service.seed_suggestions_from_index(index_id)
```

---

## `_repair_doc` — Post-Extension Safety Net (`agent/setup_executor.py`)

Runs after every `apply_extensions()` call on json_file indexes, comparing the extension output against the original raw item:

```python
def _repair_doc(processed: dict, raw: dict) -> dict:
    for key, val in list(processed.items()):

        # Case 1: extension left a nested dict/list-of-dicts untouched
        if isinstance(val, dict) or (isinstance(val, list) and val and isinstance(val[0], dict)):
            doc.update(_flatten_objects({key: val}))

        # Case 2: JS called .join() on an array of objects without .map() first
        # → produced "[object Object], [object Object], ..."
        elif isinstance(val, str) and re.search(r'\[object Object\]', val):
            raw_val = raw.get(key)
            if raw_val is not None:
                doc.update(_flatten_objects({key: raw_val}))   # re-flatten from original
            else:
                doc[key] = ""
```

`_flatten_objects()` converts a nested value to a string by checking priority keys (`title`, `name`, `src`, `url`, `href`, `value`, `label`, `text`) and falling back to joining all scalar values. Special case: `images` array backfills `doc.image` with `images[0].src` if `image` is not already set.

**Scope:** `_repair_doc` is only called in json_file rebuild paths. REST, sitemap, and GSheet rebuilds are unaffected.

---

## Suggestion Seeding

After `ts.upsert_documents()` completes, `suggestion_service.seed_suggestions_from_index(index_id)` scans all indexed documents via Typesense, calls `extractSuggestions(doc)` through the assigned suggestions extension on each doc, and inserts the returned `{query, category}` pairs into the `isp_suggestions` table. These populate the autocomplete search bar.

---

## File Locations

| File | Role |
|---|---|
| `backend/src/isp/routers/agent_setup.py` | FastAPI router — preview + setup endpoints |
| `backend/src/isp/services/infra/agent/json_sampler.py` | Load JSON file, detect root key, even sample |
| `backend/src/isp/services/infra/agent/schema_extractor.py` | Walk sample items → flat schema dict |
| `backend/src/isp/services/infra/agent/claude_analyst.py` | Claude Opus call + self-correction loop |
| `backend/src/isp/services/infra/agent/setup_executor.py` | SSE generator — creates index, extensions, runs rebuild |
| `backend/src/isp/services/infra/json_file_connector.py` | Reads JSON file for the connector |
| `frontend/src/components/settings/AgentSetup.svelte` | Agent tab UI — form, SSE terminal, preview, feedback |

---

## Auth

`/v1/agent` is in `_PUBLIC_API_PREFIXES` in `auth/middleware.py` — it bypasses ISP JWT authentication. The agent authenticates with the Anthropic API directly using the key the user provides (or the key stored in Settings → Chat Model).

---

## JSON File Placement

The API container mounts `./data:/app/data:ro` (read-only). Drop your JSON file in the project `data/` folder:

```
intelligent-search-platform/
  data/
    allbirds.json   →  /app/data/allbirds.json  (path to use in the Agent tab)
```

---

## Rebuilding an Agent Index

Agent-created indexes use the `json_file` connector. The Rebuild panel shows the source path (`SOURCE FILE: /app/data/allbirds.json`) and a **Run Rebuild** button. Clicking it re-runs the full ingest + extension + embed + repair pipeline without calling Claude again.

To change the normalizer after setup: go to Settings → Extensions, find `agent_normalizer_<id>`, edit the JS, then Run Rebuild.
