# Sitemap Indexing — Implementation Details

## Pipeline Overview

`POST /v1/indexes/{id}/ingest/sitemap` triggers a background job. The handler returns immediately with `state=queued`; poll `GET /v1/indexes/{id}/status` for progress.

```
POST /v1/indexes/{id}/ingest/sitemap
  │
  ▼
1. Fetch sitemap XML → parse all <loc> URLs
  │
  ▼
2. Filter URLs
   apply include_patterns (URL must match at least one)
   apply exclude_patterns (URL must not match any)
   run filterUrl() JS extension hook (if any assigned)
   truncate to max_pages (≤ 10,000, default 50)
  │
  ▼
3. Crawl each page (0.1 s polite delay)
   strip <script> <style> <nav> <footer> <header> <aside>
   extract og:title → <title> → <h1> → URL  (title)
   extract og:description / meta[description]  (prepended to content)
   extract body text from content_selector → fallback cascade
   extract all <meta> tags → stored as JSON in metadata field
   extract configured meta_fields → top-level doc fields
   cap content at 4,000 chars
   doc.id = MD5(url)[:16]  → re-crawling same URL is idempotent
  │
  ▼
4. Apply extensions (if any assigned to this index)
   transform(doc) can return null to drop a document
  │
  ▼
5. Embed
   text = title + content  (includes og:description + body)
   Voyage AI voyage-3 → 1024-dim vec
   empty-text docs are skipped
  │
  ▼
6. Upsert into Typesense
   ts.upsert_documents(index_id, docs)
   updates doc_count in PostgreSQL
```

### Distributed job lock

A Redis-backed distributed lock prevents two ingest/rebuild jobs from running on the same index concurrently across multiple ISP instances. Returns HTTP 409 if a job is already in progress.

---

## Document Fields Produced

| Field | Source | Notes |
|---|---|---|
| `id` | MD5(url)[:16] | Deterministic — re-crawl overwrites existing doc |
| `title` | `og:title` → `<title>` → `<h1>` → URL | Prefers OG title |
| `content` | `og:description` + body text | Description prepended for embedding quality |
| `url` | `<loc>` URL from sitemap | |
| `source_type` | `"sitemap"` | Fixed |
| `indexed_at` | Unix timestamp | Set at crawl time |
| `metadata` | All `<meta>` tags as JSON string | Available to extensions |
| _(custom)_ | Configured `meta_fields` mappings | Top-level filterable fields |

---

## Content Selector Fallback Cascade

When `content_selector` is not set (or matches nothing), ISP tries these CSS selectors in order — first match wins:

```
article → main → [role='main'] → .content → #content → .post-content → body
```

Pages that produce empty content after stripping and selector extraction are **silently skipped** — no embed, no upsert, no error.

---

## Embedding Strategy

The embedding is built from `title + content` (already the behaviour in `_run_ingest`):

```
title   = og:title or <title>               ← topic signal
content = og:description + body text        ← curated summary + detail
```

Using `og:description` alone (150–160 chars) is too thin for good semantic search. Prepending it to the body gives the embedding both the curated summary and the specific terms users search for — detailed how-to steps, feature names, and long-tail keywords live in the body, not the description.

---

## Meta Tag Extraction

### og:description vs meta[name=description]

Many pages set both `og:description` and `meta[name=description]` to the same value. ISP reads only one to avoid duplicating text in the embedding:

```python
meta_desc = (soup.find("meta", attrs={"property": "og:description"}) or
             soup.find("meta", attrs={"name": "description"}))
```

`og:description` is preferred — it is written specifically for sharing contexts and is usually more polished than the generic description tag.

Similarly for title:

```python
og_title = soup.find("meta", attrs={"property": "og:title"})
title = og_title["content"] if og_title else (title_tag.get_text() if title_tag else url)
```

---

### All meta tags → `metadata` JSON

Every `<meta>` tag on the page is collected into a dict and stored as a JSON string in the `metadata` field. This makes all meta attributes available to extensions without requiring any schema changes.

```python
meta = {}
for tag in soup.find_all("meta"):
    key = tag.get("property") or tag.get("name")
    val = tag.get("content")
    if key and val:
        meta[key] = val
doc["metadata"] = json.dumps(meta)
```

Example `metadata` value for a support page:

```json
{
  "og:title":       "Product Setup Guide | Brand Support",
  "og:description": "Learn how to set up your device, find answers to support questions, and access online tutorials.",
  "og:site_name":   "Brand Support",
  "og:type":        "website",
  "article-author": "Jane Smith",
  "og:date":        "2024-04-13T00:00:00"
}
```

### Configurable field extraction → `meta_fields`

For meta attributes that need to be **filterable in Typesense**, declare them in `SitemapConnectorConfig.meta_fields`. The sitemap crawler promotes those keys to top-level document fields.

```json
{
  "url": "https://example.com/sitemap.xml",
  "max_pages": 50,
  "meta_fields": {
    "article-author": "author",
    "og:date":        "published_at",
    "og:site_name":   "site_name"
  }
}
```

Result — each crawled document gets top-level fields:

```json
{
  "id":           "3a9f...",
  "title":        "Product Setup Guide",
  "content":      "Learn how to set up your device... [body text]",
  "author":       "Jane Smith",
  "published_at": "2024-04-13T00:00:00",
  "site_name":    "Brand Support"
}
```

These fields flow through two layers before reaching Typesense:

**1. `_ensure_isp_schema` promotes scalar extras to top-level** (`backend/src/isp/routers/indexes.py`)

Any extra field with a scalar value (`str`, `int`, `float`, `bool`) that is not already a declared ISP field is passed through to the Typesense document as a top-level field:

```python
for k, v in extra.items():
    if isinstance(v, (str, int, float, bool)) and k not in out:
        out[k] = v
```

**2. Typesense wildcard field accepts dynamic fields** (`backend/src/isp/services/infra/typesense_client.py`)

The collection schema includes a `.*` auto field so Typesense indexes any top-level field without requiring a schema change per new meta attribute:

```python
{"name": ".*", "type": "auto"}
```

Adding a new `meta_fields` mapping in the UI immediately takes effect at the next ingest — no Typesense collection recreation needed.

> **Note:** The `.*` wildcard field is only present in collections created after this change. Existing collections require a rebuild to gain `meta_fields` filtering support.

---

## User Agent Override

By default ISP sends `User-Agent: ISP-Crawler/1.0` with every crawl request. Many sites (Cloudflare-protected, e-commerce, carrier sites) return **403 Forbidden** to non-browser user agents.

Set `user_agent` in the connector config to override:

```json
{
  "url": "https://example.com/sitemap.xml",
  "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
```

The crawler uses this value for both the sitemap XML fetch and every page crawl request.

| User Agent | Result |
|---|---|
| `ISP-Crawler/1.0` (default) | Blocked by bot-protected sites (403) |
| Browser UA string | Accepted by most sites |

> **Note:** A browser UA bypasses basic bot detection. Sites using Cloudflare JS challenges, cookie validation, or TLS fingerprinting may still block the crawler — those require a headless browser and are out of scope for ISP.

---

## Configuration in the ISP Admin UI

### Create Wizard

`SitemapConnectorConfig` is set in the **Create Wizard** (Sitemap connector tab) when adding a new source and stored in the PostgreSQL `connectors` JSONB column.

### Connector tab (per-index edit)

After a source is created, go to **Configure → Connector** tab to edit the connector settings without triggering a rebuild. Changes take effect on the next ingest or rebuild.

```
┌──────────────────────────────────────────────────────────┐
│  [Overview] [Connector] [Documents] [Search] [Api]       │
│  ─────────────────────────────────────────────────────── │
│  Sitemap URL     [https://example.com/sitemap.xml      ] │
│  Max pages       [50                    ] (optional)     │
│  Content sel.    [article               ] (optional)     │
│  User Agent      [                      ] (optional)     │
│                                                          │
│  Include patterns (URL must contain at least one)        │
│  [/support/                                        ] [×] │
│  [/newsroom/                                       ] [×] │
│  [+ Add include pattern]                                 │
│                                                          │
│  Exclude patterns (URL must not contain any)             │
│  [/tag/                                            ] [×] │
│  [/live/                                           ] [×] │
│  [+ Add exclude pattern]                                 │
│                                                          │
│  Meta field mappings                  [+ Add field]      │
│  ┌──────────────────┬──────────────────────┬───┐        │
│  │ Meta tag name    │ ISP field name        │   │        │
│  ├──────────────────┼──────────────────────┼───┤        │
│  │ article-author   │ author                │ × │        │
│  │ og:date          │ published_at          │ × │        │
│  └──────────────────┴──────────────────────┴───┘        │
│                                       [Save connector]   │
└──────────────────────────────────────────────────────────┘
```

Saving calls `POST /v1/indexes/{id}/connectors/sitemap`, which replaces the existing sitemap connector config and returns the updated `IndexMeta`. No crawl is triggered.

### Rebuild panel

The **↺ Rebuild** button opens a modal that shows a sitemap form (not the REST curl form) for sitemap indexes. All fields are pre-populated from the saved connector config. The user can modify any field before running the rebuild — the modified config is sent as the rebuild body and saved back to the index on completion.

### Where the config lives

| Layer | File | What changes |
|---|---|---|
| Backend model | `backend/src/isp/models/index.py` | `meta_fields`, `user_agent`, `include_patterns`, `exclude_patterns` fields; `max_pages` cap 10,000 |
| Frontend type | `frontend/src/lib/types/index.types.ts` | Matching fields added to `SitemapConnectorConfig` interface |
| Create UI | `frontend/src/components/sources/CreateWizard.svelte` | Multi-row pattern editors, meta field mapping editor, user agent input |
| Edit UI | `frontend/src/components/sources/IndexDetail.svelte` | Connector tab with same full form; state uses plain `let` (not `$:`) to prevent Svelte reactive re-derivation from wiping user edits |
| Rebuild UI | `frontend/src/components/sources/RebuildPanel.svelte` | Detects connector type; shows sitemap form instead of REST curl form |
| Connector | `backend/src/isp/services/infra/sitemap_connector.py` | Uses all config fields at crawl time |

---

## Extension Hooks

ISP supports two extension hooks for sitemap indexing — one before crawling (URL level) and one after (content level). Both can be defined in the same extension file.

```js
// @name       Newsroom Filter
// @run_order  0
// @assign     newsroom

// Hook 1 — runs BEFORE crawl, only has the URL string
function filterUrl(url) {
  return url.includes('/newsroom/') && !url.includes('/tag/');
}

// Hook 2 — runs AFTER crawl, has full page content + metadata
function transform(doc) {
  var meta = doc.metadata ? JSON.parse(doc.metadata) : {};
  if (meta['og:site_name'] !== 'Brand Newsroom') return null;
  if (!meta['og:date']) return null;
  return doc;
}
```

### Pipeline with both hooks

```
10,000 URLs from sitemap
  ↓
include_pattern / exclude_pattern   (substring, built-in config)
  ↓
filterUrl()  ← NEW: URL-level JS hook, no HTTP fetch
  e.g. drops URLs not under /newsroom/  →  1,200 remain
  ↓
truncate to max_pages
  e.g. max_pages = 500               →  500 to crawl  (cap is 10,000)
  ↓
crawl 500 pages (fetch HTML)
  ↓
transform()  ← existing: full doc filter, content-aware
  e.g. drops pages missing og:site_name  →  380 pass
  ↓
embed 380 docs
  ↓
upsert 380 into Typesense
```

### What each hook can filter on

| Hook | Runs | Has access to | Use for |
|---|---|---|---|
| `filterUrl(url)` | Before crawl | URL string only | Path patterns, URL keywords, file extensions |
| `transform(doc)` | After crawl | Full HTML content, all meta tags, `metadata` JSON | `og:site_name`, `og:date`, page content, any meta attribute |

### URL filtering layers

ISP has four layers of URL filtering, applied in order:

| Config | Where | Use when |
|---|---|---|
| `include_patterns` | Create Wizard UI (multi-row) | URL must contain at least one pattern — e.g. `/newsroom/`, `/blog/` |
| `exclude_patterns` | Create Wizard UI (multi-row) | URL must not contain any pattern — e.g. `/tag/`, `/live/` |
| `filterUrl()` | Extension code (JS) | Complex logic, multiple conditions, regex |
| `transform()` | Extension code (JS) | Content-based filtering — requires fetched page |

Use `include_pattern` / `exclude_pattern` for straightforward cases — no code needed. Only write `filterUrl()` when the URL decision requires logic that cannot be expressed as a single substring (e.g. multiple path conditions, URL segment parsing, file extension checks).

### Why two hooks matter

`filterUrl()` eliminates URLs cheaply — no HTTP fetch, no HTML parse. Without it, `max_pages` truncates blindly from the top of the sitemap and the extension never sees URLs beyond that cap. With `filterUrl()`, the full URL list is pre-filtered before the cap is applied, so `max_pages` works on the relevant subset.

---

## Extension-Based Content Filtering

`transform(doc)` returns `null` to drop a document — ISP will not embed or upsert it. Since all meta tags are stored in `metadata` JSON, any meta attribute is available:

```js
function transform(doc) {
  var meta = doc.metadata ? JSON.parse(doc.metadata) : {};
  if (meta['og:site_name'] !== 'Brand Newsroom') return null;
  return doc;
}
```

Other use cases:

```js
// Drop pages older than 2 years
function transform(doc) {
  var meta = doc.metadata ? JSON.parse(doc.metadata) : {};
  var date = meta['og:date'] || meta['article:published_time'];
  if (date && new Date(date) < new Date('2023-01-01')) return null;
  return doc;
}

// Promote og:image to metadata for PLP card display
function transform(doc) {
  var meta = doc.metadata ? JSON.parse(doc.metadata) : {};
  if (meta['og:image']) {
    var m = doc.metadata ? JSON.parse(doc.metadata) : {};
    m.image = meta['og:image'];
    doc.metadata = JSON.stringify(m);
  }
  return doc;
}
```

---

## Files Changed

| File | Change |
|---|---|
| `backend/src/isp/models/index.py` | `meta_fields`, `user_agent` added; `include_pattern`/`exclude_pattern` (str) replaced with `include_patterns`/`exclude_patterns` (list[str]); `max_pages` cap raised 500 → 10,000; optional fields typed `Optional[str]` |
| `backend/src/isp/services/infra/sitemap_connector.py` | Refactored into small helpers (`_collect_meta`, `_best_title`, `_best_description`, `_extract_content`); `og:title`/`og:description` preferred; all meta → `metadata` JSON; `meta_fields` → top-level fields; multi-pattern include/exclude filtering; `user_agent` used for all HTTP requests; `crawl()` accepts `ext_records` and calls `filter_urls()` before cap |
| `backend/src/isp/services/infra/extension_runner.py` | Added `_filter_urls_sync()` + `filter_urls()` — calls `filterUrl(url)` JS hook per URL; extensions without `filterUrl` silently skipped; runtime errors keep the URL |
| `backend/src/isp/routers/indexes.py` | `_ensure_isp_schema` promotes scalar extra fields to top-level Typesense fields; `ext_records` passed to `sitemap_connector.crawl()`; `rebuild` endpoint accepts `ConnectorConfig` union (REST + sitemap); `_run_rebuild` has sitemap branch; `POST /connectors/sitemap` replaces (not appends) existing sitemap connector |
| `backend/src/isp/services/infra/typesense_client.py` | Added `{"name": ".*", "type": "auto"}` wildcard to collection schema for dynamic `meta_fields` support |
| `frontend/src/lib/types/index.types.ts` | Added `meta_fields`, `user_agent`, `include_patterns`, `exclude_patterns` to `SitemapConnectorConfig`; removed singular `include_pattern`/`exclude_pattern` |
| `frontend/src/components/sources/CreateWizard.svelte` | Multi-row editors for include/exclude patterns and meta fields; `user_agent` input added; all optional fields marked with hint labels |
| `frontend/src/components/sources/IndexDetail.svelte` | New **Connector** tab (between Overview and Documents) with full sitemap config editor; form state uses plain `let` variables initialized in `onMount` — avoids Svelte `$:` re-derivation overwriting user edits mid-type |
| `frontend/src/components/sources/RebuildPanel.svelte` | Detects `index.connectors[0]?.type`; sitemap indexes show sitemap form pre-populated from saved config; REST indexes retain existing curl paste form |
| `frontend/src/components/sources/RebuildPanel.helpers.ts` | Added `getExistingSitemap(index)` |
| `frontend/src/lib/api.ts` | `rebuild()` widened to `RestConnectorConfig \| SitemapConnectorConfig`; added `saveSitemapConnector()` → `POST /indexes/{id}/connectors/sitemap` |
| `frontend/src/lib/repository/IndexRepository.ts` | `rebuild` and `saveSitemapConnector` forwarded to `api.ts` |
| `frontend/src/lib/services/indexes/IndexService.ts` | Same signatures propagated to service layer |

---

## References

> Citations below are sourced from training knowledge (not live browsing). DOI and arXiv links are provided where known.

1. **Koster, M.** (1994). "A Standard for Robot Exclusion." *WWW Robots Page*. URL: [robotstxt.org](https://www.robotstxt.org/orig.html) — Defines the `robots.txt` standard and polite crawling conventions; ISP's 0.1-second polite delay between page fetches follows the crawl-rate guidelines introduced here.

2. **Sitemaps.org Consortium** (Google, Yahoo, Microsoft). (2006). *Sitemaps XML Format Specification v0.9*. URL: [sitemaps.org/protocol.html](https://www.sitemaps.org/protocol.html) — ISP's sitemap connector parses the `<loc>` URL elements defined by this standard; `<lastmod>` and `<changefreq>` are also available for future incremental-crawl support.

3. **Finn, A., Sundaram, H., & Lehmberg, O.** (2010). "Boilerplate Detection Using Shallow Text Features." *Proceedings of the 3rd ACM International Conference on Web Search and Data Mining (WSDM 2010)*, pp. 441–450. DOI: [10.1145/1718487.1718542](https://doi.org/10.1145/1718487.1718542) — Boilerplate removal: ISP strips `<script>`, `<style>`, `<nav>`, `<footer>`, `<header>`, `<aside>` — the same tag-class exclusions used in boilerplate-detection systems to isolate content from navigation chrome.

4. **Open Graph Protocol.** (2010). *The Open Graph Protocol*. Facebook / ogp.me. URL: [ogp.me](https://ogp.me) — Defines `og:title`, `og:description`, `og:image`, `og:date` meta properties; ISP preferentially extracts these for `title`, `content`, and `metadata` fields because OG tags are curated for rich-link contexts.

5. **Devlin, J., Chang, M.-W., Lee, K., & Toutanova, K.** (2019). "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding." *Proceedings of NAACL-HLT 2019*. arXiv: [1810.04805](https://arxiv.org/abs/1810.04805) — Contextual embeddings improve quality when the input text is semantically rich; ISP's choice to prepend `og:description` to body text before embedding maximises the semantic density of the vector representation.

6. **Risvik, K. M., & Michelsen, R.** (2002). "Search Engines and Web Dynamics." *Computer Networks*, 39(3), 289–302. DOI: [10.1016/S1389-1286(02)00213-0](https://doi.org/10.1016/S1389-1286(02)00213-0) — Web dynamics and freshness; motivates `doc.id = MD5(url)[:16]` for deterministic re-crawl: same URL always overwrites the same document ID so stale content is replaced without manual deletion.

7. **Cloudflare, Inc.** (2023). *Cloudflare Bot Management*. URL: [cloudflare.com/products/bot-management](https://www.cloudflare.com/products/bot-management) — User-agent-based bot protection is the first barrier to crawling; ISP's configurable `user_agent` field allows operators to supply a browser UA string to bypass basic bot detection (Cloudflare JS challenges require a headless browser and are explicitly out of scope).
