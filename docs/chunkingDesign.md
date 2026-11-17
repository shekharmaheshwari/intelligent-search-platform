# Document Chunking Design

Status: **Implemented (phase 1)** · Date: 2026-11-17

Phase 1 is in place: chunking for sitemap, REST, gsheet, JSON-file and manual
documents, one search result per document, chunk-aware chat context, a
one-row-per-document Documents list, and per-index settings. Deferred: the
chunk view in the Documents tab (§8.3) and a settings toggle in the UI. See
§13 for what was built.

## 1. Problem

ISP indexes every source document as exactly **one Typesense record with one
embedding vector**. Long text is truncated, never split:

| Where | Truncation |
|---|---|
| Sitemap pages — `_extract_page` in sitemap_connector.py | `content[:4000]` (meta description first) |
| REST / gsheet / JSON ingest | no limit; the whole field is embedded as one vector |
| External collections, display only — `_map_external_doc` | `content[:1000]` (read path, not ingest) |
| `chat_content_*.js` extensions | long description `[:400]` |
| `card_summary_*.js` extensions | long description `[:220]` |
| Chat LLM context — [retrieval.py:177](../backend/src/isp/services/infra/chat/retrieval.py#L177) | `chatContent` or `content[:300]` |

This suits short, structured product catalogs. For long-form content it causes
two problems:

- **Lost text.** Anything past the cap can't be found by keyword search,
  semantic search or chat.
- **Diluted vectors.** One embedding covering a whole page averages many topics
  together, so a question about one paragraph of the page matches poorly.

The extensions don't solve this. A transform extension maps one document to one
dict ([extension_runner.py:67-71](../backend/src/isp/services/infra/extension_runner.py#L67-L71)).
Pre-processors could fan records out, but they only run on REST responses, not
on sitemap crawls or manual documents.

## 2. Goals / non-goals

**Goals**
- Split long documents into overlapping chunks, each with its own vector.
- Search results still show **one row per source document**.
- Chat/RAG gets the **most relevant passages**, not the first 300 chars.
- Opt-in per index. Indexes that don't enable it behave exactly as they do today.

**Non-goals**
- Chunking product catalogs (already short, and `chatContent` is curated per doc).
- Chunking through JS extensions.
- Semantic or LLM-based chunking (possible later, see §9).

## 3. Approach

Chunking is a **built-in Python step in the ingest pipeline**, placed after
extensions and before embedding, so it works on the final cleaned `content`:

```
raw → _isp_type → extensions → _generic_catalog_mapper → CHUNK → embed → upsert
```

Why Python and not an extension:
- It must cover every source: REST, gsheet, sitemap and manual documents.
- Splitting on sentences and paragraphs is much easier in Python than in the V8
  sandbox.
- Chunk IDs, grouping and deletes have to match backend behaviour, so the
  backend should own them.

## 4. Configuration

Stored on `IndexMeta` and edited from the index settings UI:

```python
class ChunkingConfig(BaseModel):
    enabled: bool = False
    chunk_tokens: int = 400        # target size (~4 chars/token → ~1600 chars)
    overlap_tokens: int = 60       # carried into the next chunk
    min_chunk_tokens: int = 50     # a smaller tail is merged into the previous chunk
    max_content_chars: int = 50_000  # truncation cap that replaces 1000/4000 when enabled
```

### 4.1 Two levels: index switch, then document size

Chunking is decided at two levels:

1. **Per index (`enabled`):** whether this index is allowed to chunk at all.
2. **Per document (automatic):** in an enabled index, each document is checked
   by size on every ingest:

   ```python
   if len(content) <= chunk_tokens * 4:   # ≤ ~1,600 chars at defaults
       keep as 1 record                    # original id, chunk_index 0
   else:
       split into N chunks
   ```

   So in one sitemap index, a short contact page stays as one record and a long
   guide becomes several chunks. Nobody chooses per document.

### 4.2 Why keep an index-level switch

Size alone could decide, since product records are mostly short. The switch
stays for three reasons:

- **Content limits depend on it.** REST content is cut at 1,000 chars, which is
  below the split threshold. The limit is raised to `max_content_chars` only
  when chunking is enabled. Without a switch, some other rule would have to
  decide when to raise it.
- **Products shouldn't be split even when long.** A phone with a long
  description is still one product. As 3 records, sorting, popularity and boost
  rules, and superlative queries ("cheapest phone") would treat it as three
  products, or would all need to group by `parent_id` first.
- **Cost.** More records means more embedding calls and more Typesense memory,
  so it's a choice made per index (§10).

### 4.3 Defaults by source type

So operators don't have to think about it, `enabled` defaults from the index's
connector when the index is created or its first connector is added:

| Index source | Default `enabled` | Reason |
|---|---|---|
| Sitemap | **On** | Pages range from short to very long. |
| No connector (manual documents only) | **On** | Free text written by hand, often long. |
| REST / gsheet catalogs | **Off** | Structured records, one product per record. |

Notes:
- The default is applied **once**. After that the operator can change it in the
  index settings, and a later connector change doesn't override that choice.
- An index has one setting. Manual documents added to a REST catalog index
  follow that index's setting (off by default).
- Existing indexes keep `enabled: false` until an operator turns it on and
  rebuilds. There's no silent migration.

Changing `enabled`, `chunk_tokens` or `overlap_tokens` requires a rebuild, like
changing the embedding model does today.

## 5. Chunk document model

A source document becomes N Typesense records:

| Field | Value |
|---|---|
| `id` | `{parent_id}.chunk{chunk_index}` (e.g. `a1b2c3d4e5f6a7b8.chunk2`). The `.` is reserved: source ids are limited to `[A-Za-z0-9_-]`, so a chunk id can never collide with a source id. Not `#`: Typesense ids go into URLs. |
| `parent_id` | original document id (**new field**, `string`, `facet: true`) |
| `chunk_index` | 0-based position (**new field**, `int32`, sortable) |
| `chunk_count` | total chunks for the parent (**new field**, `int32`) |
| `chunk_tail` | `true` on chunks 1..n-1 only (**new field**, `bool`). `chunk_tail:!=true` matches docs that lack the field, so it is a safe "one row per document" filter on every collection. |
| `content` | the chunk's text window |
| `title`, `url`, `source_type`, facets, static fields | copied from the parent |
| `vec` | embedding of `title + " " + chunk content` |
| image fields | copied, but image indexing only exports first chunks (`chunk_tail:!=true`), so only chunk 0 gets `img_vec` and image search has no duplicates |

Short documents (`len(content) <= chunk_tokens * 4`) still get `parent_id` and
`chunk_index: 0` and keep their **original id**. Grouping and deletes then work
the same way for every document, and indexes that enable chunking keep the same
IDs for their short documents.

**Every record in a chunked collection must have `parent_id`.** Typesense puts
all docs that lack the `group_by` field into a single group, so one unstamped
doc would merge with every other unstamped doc in search results. Manual
documents are therefore stamped too (never split, see §7).

Every chunk carries the title, and `_build_embed_text` already joins
`title + content + chatContent`, so each vector knows which page it came from
with no change to the embedding code.

## 6. Chunker

New module `backend/src/isp/services/infra/chunker.py`:

```python
def chunk_doc(doc: dict, cfg: ChunkingConfig) -> list[dict]:
    text = doc.get("content", "")
    if not cfg.enabled or len(text) <= cfg.chunk_tokens * 4:
        return [{**doc, "parent_id": doc["id"], "chunk_index": 0, "chunk_count": 1}]

    windows = split_text(text, cfg.chunk_tokens, cfg.overlap_tokens, cfg.min_chunk_tokens)
    out = []
    for i, w in enumerate(windows):
        chunk = {**doc,
                 "id": f"{doc['id']}.chunk{i}",
                 "parent_id": doc["id"],
                 "chunk_index": i,
                 "chunk_count": len(windows),
                 "content": w}
        if i > 0:
            for f in IMAGE_SYSTEM_FIELDS:
                chunk.pop(f, None)
        out.append(chunk)
    return out


def chunk_docs(docs: list[dict], cfg: ChunkingConfig) -> list[dict]:
    return [c for d in docs for c in chunk_doc(d, cfg)]
```

`split_text` splits in this order:
1. Paragraphs (`\n\n`, block boundaries kept by the sitemap extractor).
2. Sentences inside a paragraph that is too long.
3. Hard character cut only for a single sentence longer than `chunk_tokens`.

It fills each window up to `chunk_tokens` and starts the next window
`overlap_tokens` back, aligned to a sentence start. Token counts use a
`chars / 4` estimate. That's close enough for sizing and avoids loading a
tokenizer.

The sitemap connector currently collapses all whitespace
(`" ".join(content.split())`), which throws away paragraph breaks. When chunking
is enabled it should keep `\n\n` between blocks.

## 7. Pipeline integration

| Path | Location | Change |
|---|---|---|
| REST / gsheet ingest | `_run_ingest` | `docs = chunk_docs(docs, cfg)` before `_embed_in_place` |
| REST rebuild | `_run_rebuild` | same |
| Sitemap ingest | `_ingest_sitemap_batched` ([indexes.py:672-679](../backend/src/isp/routers/indexes.py#L672-L679)) | chunk each batch after extensions |
| Sitemap rebuild | `_rebuild_sitemap_batched` ([indexes.py:717-727](../backend/src/isp/routers/indexes.py#L717-L727)) | same |
| Manual documents | POST / PUT documents, post-rebuild re-upsert | chunk before embed |
| Truncation caps | `_map_external_doc`, `sitemap_connector` | use `max_content_chars` when chunking is enabled |
| Schema | `_ensure_isp_schema` | add `parent_id`, `chunk_index`, `chunk_count` |

Batch sizes: a sitemap batch of `_SITEMAP_BATCH` pages becomes several times
more records. Voyage calls are already capped at 128 per request inside
`embed_texts`, so no change is needed there. Ingest progress should keep counting
**pages**, not chunks.

## 8. Read paths

### 8.1 Search (one row per document)

In `search_documents` and `search_with_vec`, when the index has chunking enabled:

```python
params |= {"group_by": "parent_id", "group_limit": 1}
```

The best-scoring chunk represents each document. The response handler flattens
`grouped_hits` into the existing hit shape, so the frontend doesn't change. The
winning chunk's highlight works as a "match in context" snippet, which is
better than the start of the page.

Pagination and `found` then count groups (documents) instead of chunks, which is
what users expect.

### 8.2 Chat / RAG (passages, not documents)

In [retrieval.py](../backend/src/isp/services/infra/chat/retrieval.py), for
chunked indexes:
- Search **without** grouping and take the top ~20 chunks.
- Keep at most **2 chunks per `parent_id`** so one page can't fill the context.
- Pass the chunk `content` to the LLM instead of `content[:300]`, and cite the
  parent `title` / `url`.
- Optional: add the neighbouring chunks (`chunk_index ± 1`) of the top hit when
  more context is needed.

Most of the quality gain comes from this change.

### 8.3 Documents tab

Listing filters to `chunk_index:=0` and shows `chunk_count`. Opening a document
shows the full text by fetching all chunks of `parent_id`, sorted by
`chunk_index`. For manual documents the full text is already in the
`manual_documents` Postgres table.

### 8.4 Image search

Only chunk 0 carries image fields (§5), so `search_by_image_vec` needs no change.

## 9. Deletes and updates

Deletes go by parent, never by a single chunk id:

```python
ts.delete_by_filter(index_id, f"parent_id:={parent_id}")
```

- **Re-crawl / re-ingest of a page**: delete by `parent_id`, then upsert the new
  chunks. Otherwise a page that shrinks from 5 chunks to 3 leaves `#3` and `#4`
  behind.
- **Manual document PUT / DELETE**: same, keyed on the `manual-…` id.
- **Rebuild**: drops the collection, so nothing extra is needed.

## 10. Cost and capacity

Record count grows by about `avg_content_chars / 1600` per document. For
example, 5,000 pages averaging 8k chars become about 25k records. That means:
- About 5× the embedding calls and cost (Voyage) on ingest and rebuild. The
  embedding cache still deduplicates unchanged chunks across rebuilds.
- About 5× the vector memory in Typesense.
- Search latency barely changes. `group_by` adds a small cost.

This is why the setting is opt-in per index.

## 11. Rollout

1. `chunker.py` and `ChunkingConfig`, with unit tests for splitting, overlap,
   tail merge and ID scheme.
2. Schema fields, plus sitemap and manual-document ingest wiring.
3. `group_by` in search and the Documents tab grouping.
4. Chunk-aware chat retrieval.
5. Configurable truncation caps, delete-by-parent, and an index settings toggle
   in the UI.
6. Evaluate on a sitemap index with `evaluation/` (recall@k and answer quality,
   chunked vs. unchunked).
7. Turn on the source-type defaults (§4.3) for new indexes once step 6 shows an
   improvement.

## 12. Future options (see also §13 for deferred items)

- **Contextual chunk headers**: prepend the section heading (`h2`/`h3`) as well
  as the title to each chunk's embed text.
- **Semantic chunking**: split where embedding similarity between adjacent
  sentences drops.
- **Parent-document retrieval**: match on small chunks (~150 tokens) but send a
  larger surrounding window to the LLM.

## 13. Implementation (phase 1)

All chunking logic is in one pure module with no I/O. The rest of the system
calls it at a few points.

| Piece | Where | What it does |
|---|---|---|
| Chunker | `backend/src/isp/services/domain/chunking.py` | `ChunkingConfig`, `split_text`, `chunk_documents`, `top_per_parent`, family and stale filters, `applied_config` / `needs_rebuild`, source defaults |
| Tests | `backend/tests/domain/test_chunking.py`, `backend/tests/routers/test_manual_documents_rollback.py` | splitting, overlap, IDs, filters, rebuild detection, sitemap extraction; manual split, shrink, rollback, rejected chunk |
| Settings | `IndexMeta.chunking` (JSONB `indexes.chunking`), `index_service.merge_chunking` | wanted config plus `applied` (the config the collection was built with) |
| API | `GET` / `PUT /v1/indexes/{id}/chunking` | read or save settings; response has `applied` and `rebuild_required` |
| Schema | `typesense_client.create_collection`, `migrate_isp_fields` | adds `parent_id` (facet), `chunk_index`, `chunk_count`, `chunk_tail` |
| Write paths | `routers/indexes.py`: `_chunking_plan`, `_chunk`, `_upsert`, `_delete_stale_chunks`, `_mark_chunked` | sitemap, REST, gsheet and JSON ingest and rebuild; manual docs |
| Sitemap text | `sitemap_connector.crawl_batches(max_content_chars=…)` | keeps paragraph breaks and uses the larger limit only for chunked indexes |
| Search | `search_documents` / `search_with_vec(group_chunks=)` | one hit per document via Typesense `group_by: parent_id` (both hybrid legs) |
| Chat | `chat/retrieval.py`: `_build_search_params(grouped=)`, `_top_hits`, `_hit_to_card_and_block` | on chunked indexes each leg fetches 15 *documents* with up to 2 passages each (`group_by`, vector `k` ×8); at most 2 passages per document in the top 5; full record text as context (passages and short single-chunk docs); one card per document |
| Listing and counts | `list_documents`, `count_documents` | filter `chunk_tail:!=true`, so one row per document and `doc_count` counts documents |
| Images | `image_index_service.index_images` | exports first chunks only |
| Manual docs | `_save_manual_doc`, `_restore_manual_docs` | chunked with the collection's layout (plain records in unchunked indexes); routes accept chunk ids (`parent_of`) |
| Protected fields | `_FIELD_SYSTEM`, `_MANUAL_HIDDEN_FIELDS`, `get_all_fields`, manual `_RESERVED` | chunk fields can't be edited on the Fields tab or set from the manual form |

### Wanted config vs `applied`

- The stored `ChunkingConfig` fields are what the operator **wants**. If
  `enabled` isn't set, the first ingest or rebuild fills it from the source
  type (§4.3).
- `applied` holds the **config the collection was built with** (or nothing,
  when it isn't chunked). Search groups only when it is set.
- **Rebuild, or first write into an empty index:** adopts the wanted config.
- **Appends** (ingest, manual-document saves) keep using `applied`, so one
  collection never mixes chunked and unchunked records, or two chunk sizes.
  Changed sizes take effect at the next rebuild.
- **`rebuild_required`** is true whenever the wanted config differs from
  `applied`: on/off or any size (`chunking.needs_rebuild`).
- **When `applied` changes:** it changes only together with the collection.
  A rebuild clears it right before dropping the old collection
  (`_clear_applied`), so a rebuild that fails while fetching or embedding
  leaves the existing chunks grouped. It is set again (`_mark_chunked`) as soon
  as the new collection holds chunk records: after the first successful batch
  of a sitemap crawl, or right after the upsert on other paths. So a crawl
  that fails on a later batch still leaves the chunks already written grouped. Search never groups a collection whose records
  lack `parent_id`, and the worst case mid-rebuild is a few duplicate rows,
  never merged results.

### Behaviour notes

- **Paragraph cuts carry no overlap.** When a chunk ends on a paragraph break,
  the next chunk starts cleanly at the new paragraph. Overlap applies only to
  sentence cuts.
- **Each document is written all-or-nothing.** Before an append, the
  document's existing records (with vectors) are snapshotted. If any of its
  new chunks fails to import, every record of that document is reset: the
  snapshot is restored on an append, and the partial new chunks are removed on
  a rebuild. It is then counted as not written and logged. Documents written
  in full then lose the leftovers of their previous layout:
  `(parent_id:[…] || id:[…]) && chunk_count:!=N`. New chunks overwrite ids
  `0..N-1` and all carry `chunk_count = N`, so this matches only an older,
  longer layout or a pre-chunking record. Verified live: a 5-chunk document
  updated to 2 chunks, with the second new chunk rejected, is restored
  exactly.
- **`max_content_chars` applies to every source.** `chunk_documents` caps the
  text before splitting, so REST, gsheet, JSON and manual documents are capped
  too, not only sitemap pages.
- **Manual-document saves** use the same snapshot and restore
  (`_snapshot_families` / `_restore_families`), then clean up stale records. If any chunk is rejected, or
  the Postgres write fails, the snapshot is restored.
- **Hybrid search groups in Typesense on both legs.** Each leg returns up to
  `3 × per_page` distinct documents, and RRF fuses them per document. The
  vector leg widens `k` (×8, at most 2000) because it groups its k nearest
  *chunks*. Verified live: one page with 29 near chunks plus 12 other pages
  gives a full page of 10 distinct documents.
- **Ingest progress counts source documents.** `IngestStatus.indexed` and
  `doc_count` count source documents, not chunk records.
- **Deletes that guard content are strict.** `ts.delete_by_filter` raises on
  failure, unlike `delete_from_collection`. Deleting a manual document removes
  every Typesense record first and keeps the Postgres source if that fails
  (HTTP 500). Stale-chunk cleanup raises too: an ingest then reports an error,
  and a manual save rolls back to the previous version.
- **Stale cleanup runs only on chunk records.** `chunk_count:!=N` also matches
  records that lack the field, so running it for a plain record would delete
  that record itself.
- **Editorial edits (PATCH) on a split document** are applied to every chunk,
  so ranking is the same whichever chunk matches.

### Deferred

- **Documents tab chunk view (§8.3).** The list shows the first chunk with
  `chunk_count`. An expandable list of chunks isn't built yet.
- **Settings UI toggle.** Settings are API-only for now:
  `PUT /v1/indexes/{id}/chunking`.
