"""
/v1/indexes — Source management, ingest, rebuild, and document listing.

Key design decisions:
- Connector config is persisted to PostgreSQL *before* the background task starts
  so the Rebuild panel can pre-populate even if the ingest hasn't finished yet.
- Catalog write-back (external sources) goes through the JS extension pipeline:
  raw items → _isp_type injection → extensions → _generic_catalog_mapper → embed → upsert.
- _generic_catalog_mapper makes zero assumptions about upstream API field names;
  all field normalisation is the extension's responsibility.
- _map_external_doc passes all raw Typesense fields through so the expand view
  in the UI can display vec, tags, aliases, etc.
"""
from __future__ import annotations
import asyncio, hashlib, json, time
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from isp.models import (
    IndexCreate, IndexMeta, IngestStatus,
    RestConnectorConfig, SitemapConnectorConfig,
)
from isp.services import index_service, embedding_service, extension_service
from isp.services import typesense_client as ts
from isp.services import rest_connector, sitemap_connector
from isp.services.extension_runner import apply_extensions

router = APIRouter(prefix="/indexes", tags=["indexes"])


# ── Generic catalog mapper (extension-driven) ─────────────────────────────────
#
# Extensions are responsible for normalising any API response into a flat dict
# with well-known keys (display, type, url, manufacturer, price, …).
# This mapper just packages that flat dict into the Typesense catalog schema —
# it makes NO assumptions about the upstream API field names.

def _generic_catalog_mapper(item: dict, idx: int) -> dict | None:
    """
    Package an extension-normalised flat dict into the Typesense catalog schema.

    Expects the item to already have standard keys (``display``, ``type``, ``url``,
    ``manufacturer``, ``price``, etc.) — all field normalisation must happen in the
    JS extension before this function is called.

    ``metadata`` is stored as a JSON string because Typesense does not support nested
    objects as indexed fields.  The ``catalog.py`` router parses it back for search results.

    Returns None for items with no displayable name so they are silently skipped.
    """
    display = str(item.get("display") or item.get("name") or item.get("title") or "")
    if not display:
        return None

    raw_id  = str(item.get("id") or idx)
    doc_id  = f"dev_{hashlib.md5(raw_id.encode()).hexdigest()[:12]}"
    now     = int(time.time())

    price = item.get("price")
    metadata: dict = {
        "manufacturer":    str(item.get("manufacturer") or ""),
        "category":        str(item.get("category") or ""),
        "url":             str(item.get("url") or ""),
        "url_name":        str(item.get("url_name") or ""),
        "images":          list(item.get("images") or [])[:3],
        "color_options":   list(item.get("color_options") or []),
        "skus":            list(item.get("skus") or [])[:5],
        "description":     str(item.get("description") or ""),
        "operating_system": str(item.get("operating_system") or ""),
        "in_stock":        bool(item.get("in_stock", True)),
    }
    if price is not None:
        try:
            metadata["price"] = float(price)
        except (ValueError, TypeError):
            pass

    return {
        "id":         doc_id,
        "display":    display,
        "type":       str(item.get("type") or "device"),
        "aliases":    list(item.get("aliases") or []),
        "tags":       list(item.get("tags") or []),
        "segment":    ["all"],
        "popularity": int(item.get("popularity") or 0),
        "is_featured": bool(item.get("is_featured", False)),
        "created_at": now,
        "updated_at": now,
        "metadata":   json.dumps(metadata),
    }


# ── Field mapper for external (non-ISP) Typesense collections ─────────────────

def _map_external_doc(raw: dict, meta: IndexMeta) -> dict:
    """Convert a raw Typesense document to the ISP DocumentItem shape."""
    title = str(raw.get(meta.external_title_field) or raw.get("display") or raw.get("title") or raw.get("name") or "")

    # Try to extract a human-readable content string
    content = ""
    # 1. Check for an explicit content / description field
    for f in ("content", "description", "body", "text"):
        val = raw.get(f)
        if val and isinstance(val, str):
            content = val
            break
    # 2. Try to parse metadata JSON (used in T-Mobile catalog)
    if not content:
        meta_raw = raw.get("metadata")
        if meta_raw:
            try:
                md = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                content = str(md.get("description") or "")
            except Exception:
                pass
    # 3. Fall back to tags/aliases
    if not content:
        parts = []
        for f in ("aliases", "tags", "segment"):
            v = raw.get(f)
            if isinstance(v, list):
                parts.extend(str(x) for x in v)
        content = " ".join(parts)

    # URL is computed by the extension and stored in metadata.url
    url = ""
    meta_raw = raw.get("metadata")
    if meta_raw:
        try:
            md = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
            url = str(md.get("url") or "")
        except Exception:
            pass
    if not url:
        url = str(raw.get("url") or raw.get("link") or "")

    source_type = str(raw.get("type") or raw.get("source_type") or meta.external_collection)

    doc = {
        "id": str(raw.get("id", "")),
        "title": title[:200],
        "content": content[:1000],
        "url": url,
        "source_type": source_type,
        "indexed_at": int(raw.get("updated_at") or raw.get("indexed_at") or raw.get("created_at") or 0),
    }
    # Pass through all remaining raw fields (e.g. vec, tags, aliases, metadata, popularity)
    for k, v in raw.items():
        if k not in doc:
            doc[k] = v
    return doc


def _wrap_external_results(raw: dict, meta: IndexMeta) -> dict:
    """Map every hit in a raw Typesense response to ISP DocumentItem shape."""
    hits = [{"document": _map_external_doc(h["document"], meta)} for h in raw.get("hits", [])]
    return {"hits": hits, "found": raw.get("found", 0), "page": raw.get("page", 1)}


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[IndexMeta], summary="List all indexes")
async def list_indexes():
    """Return every index registered in ISP, ordered by creation date."""
    return await index_service.list_indexes()


@router.post("/discover", response_model=list[IndexMeta], summary="Discover external Typesense collections")
async def discover_indexes():
    """
    Scan Typesense for collections that are not yet tracked by ISP and register
    them as read-only external sources.  Safe to call repeatedly — already-known
    collections are skipped.

    Newly discovered catalog indexes are automatically assigned the seeded
    extension (e.g. T-Mobile Catalog Mapper) if one exists in the library.
    """
    from isp.main import _TMOBILE_EXT_NAME
    new_indexes = await index_service.discover_external_collections()
    if new_indexes:
        exts = await extension_service.list_extensions()
        tmo = next((e for e in exts if e["name"] == _TMOBILE_EXT_NAME), None)
        if tmo:
            for idx in new_indexes:
                if idx.is_external:
                    await extension_service.assign_extension_if_new(idx.id, tmo["id"], run_order=0)
    return new_indexes


@router.post("", response_model=IndexMeta, status_code=status.HTTP_201_CREATED, summary="Create a new index")
async def create_index(body: IndexCreate):
    """
    Create a new ISP-managed index and provision its Typesense collection.
    The index starts empty; use a connector + ingest or rebuild to populate it.
    """
    meta = await index_service.create_index(body.name, body.description)
    ts.create_collection(meta.id)
    return meta


@router.get("/{idx_id}", response_model=IndexMeta, summary="Get index metadata")
async def get_index(idx_id: str):
    """Return the full metadata record for a single index, including connector config and doc count."""
    meta = await index_service.get_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    return meta


@router.delete("/{idx_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an index")
async def delete_index(idx_id: str):
    """
    Permanently delete an index and all its documents from Typesense.
    For catalog write-back sources (e.g. Phones, Smartwatches) this removes only
    the documents belonging to that source via the stored filter; the shared
    `catalog` collection itself is not dropped.
    """
    meta = await index_service.get_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    await index_service.delete_index(idx_id)
    if meta.external_collection:
        ts.delete_from_collection(meta.external_collection, meta.external_filter)
    else:
        ts.drop_collection(idx_id)


@router.get("/{idx_id}/status", response_model=IngestStatus, summary="Get ingest / rebuild status")
async def get_status(idx_id: str):
    """
    Poll the current background-job status for an index.
    `state` is one of `idle`, `queued`, `running`, `done`, or `error`.
    """
    return await index_service.get_status(idx_id)


# ── Connectors ────────────────────────────────────────────────────────────────

@router.post("/{idx_id}/connectors/rest", response_model=IndexMeta, summary="Save a REST connector config")
async def add_rest_connector(idx_id: str, cfg: RestConnectorConfig):
    """
    Persist a REST connector configuration for the index without triggering an ingest.
    Use this to pre-configure the source; call `/ingest/rest` or `/rebuild` to actually fetch data.
    """
    meta = await index_service.add_connector(idx_id, {"type": "rest", **cfg.model_dump()})
    if not meta:
        raise HTTPException(404, "Index not found")
    return meta


@router.post("/{idx_id}/connectors/sitemap", response_model=IndexMeta, summary="Save a sitemap connector config")
async def add_sitemap_connector(idx_id: str, cfg: SitemapConnectorConfig):
    """
    Persist a sitemap connector configuration for the index without triggering a crawl.
    Use this to pre-configure the source; call `/ingest/sitemap` to start crawling.
    """
    meta = await index_service.add_connector(idx_id, {"type": "sitemap", **cfg.model_dump()})
    if not meta:
        raise HTTPException(404, "Index not found")
    return meta


# ── Ingest ────────────────────────────────────────────────────────────────────

async def _run_ingest(idx_id: str, connector_cfg: dict) -> None:
    """
    Background task: fetch → apply extensions → embed → append to ISP collection.

    Extension records are loaded from the database at run time so changes made
    via the Extensions UI take effect on the next ingest without a restart.
    """
    from isp.models import IngestStatus
    s = IngestStatus(index_id=idx_id, state="running")
    await index_service.set_status(s)
    try:
        ctype = connector_cfg.get("type")
        cfg_data = {k: v for k, v in connector_cfg.items() if k != "type"}

        if ctype == "rest":
            cfg = RestConnectorConfig(**cfg_data)
            docs = await rest_connector.fetch_and_parse(cfg)
        elif ctype == "sitemap":
            cfg = SitemapConnectorConfig(**cfg_data)
            docs = await sitemap_connector.crawl(cfg, on_progress=None)
        else:
            raise ValueError(f"Unknown connector type: {ctype}")

        s.total = len(docs)
        await index_service.set_status(s)

        ext_records = await extension_service.list_index_extensions(idx_id)
        docs = await apply_extensions(docs, ext_records)

        texts = [f"{d['title']} {d['content']}" for d in docs]
        vectors = await embedding_service.embed_texts(texts)
        for doc, vec in zip(docs, vectors):
            if vec:
                doc["vec"] = vec

        ok = ts.upsert_documents(idx_id, docs)
        s.indexed = ok
        s.state = "done"

        meta = await index_service.get_index(idx_id)
        if meta:
            meta.doc_count = ts.count_documents(idx_id)
            meta.connectors = [connector_cfg]
            await index_service.update_index(meta)

    except Exception as exc:
        s.state = "error"
        s.error = str(exc)

    await index_service.set_status(s)


@router.post("/{idx_id}/ingest/rest", response_model=IngestStatus, summary="Ingest from a REST endpoint")
async def ingest_rest(idx_id: str, cfg: RestConnectorConfig, bg: BackgroundTasks):
    """
    Fetch documents from a REST API, generate embeddings, and **append** them to the index.
    The connector config is persisted so the Rebuild panel can pre-populate it later.
    Returns immediately with `state=queued`; poll `/status` to track progress.
    """
    meta = await index_service.get_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    # Persist connector immediately so Rebuild panel pre-populates even before ingest finishes
    meta.connectors = [{"type": "rest", **cfg.model_dump()}]
    await index_service.update_index(meta)
    bg.add_task(_run_ingest, idx_id, {"type": "rest", **cfg.model_dump()})
    from isp.models import IngestStatus as IS
    s = IS(index_id=idx_id, state="queued")
    await index_service.set_status(s)
    return s


@router.post("/{idx_id}/ingest/sitemap", response_model=IngestStatus, summary="Ingest from a sitemap")
async def ingest_sitemap(idx_id: str, cfg: SitemapConnectorConfig, bg: BackgroundTasks):
    """
    Crawl all URLs listed in a sitemap XML, extract page content, generate embeddings,
    and **append** them to the index.
    Returns immediately with `state=queued`; poll `/status` to track progress.
    """
    meta = await index_service.get_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    meta.connectors = [{"type": "sitemap", **cfg.model_dump()}]
    await index_service.update_index(meta)
    bg.add_task(_run_ingest, idx_id, {"type": "sitemap", **cfg.model_dump()})
    from isp.models import IngestStatus as IS
    s = IS(index_id=idx_id, state="queued")
    await index_service.set_status(s)
    return s


# ── Rebuild ──────────────────────────────────────────────────────────────────

class RebuildRequest(RestConnectorConfig):
    """REST config for re-indexing. Overwrites all docs in this source."""
    pass


async def _run_rebuild(idx_id: str, cfg_dict: dict) -> None:
    """
    Background task for full rebuild.

    Two paths depending on ``meta.external_collection``:

    **Catalog write-back path** (external sources like Phones, Accessories):
      raw API items → _isp_type injection → JS extensions → _generic_catalog_mapper
      → embed → upsert to shared catalog Typesense collection.
      The existing catalog docs for this source's filter are *overwritten*, not appended.

    **ISP-managed path** (standard sources):
      fetch + parse → JS extensions → embed → drop+recreate isp_{id} → upsert.
      Drop+recreate ensures deleted API items are removed from Typesense.
    """
    from isp.models import IngestStatus
    s = IngestStatus(index_id=idx_id, state="running")
    await index_service.set_status(s)
    try:
        meta = await index_service.get_index(idx_id)
        if not meta:
            raise ValueError("Index not found")

        cfg = RestConnectorConfig(**{k: v for k, v in cfg_dict.items() if k != "type"})

        # Fetch raw items (pre-mapping) so we can map to either ISP or catalog schema
        raw_items = await rest_connector.fetch_raw_items(cfg)
        if not raw_items:
            raise ValueError("0 items fetched — API returned no usable data.")

        s.total = len(raw_items)
        await index_service.set_status(s)

        if meta.external_collection:
            # ── Write-back mode: extensions transform raw items → generic mapper packages them ──
            type_label = meta.external_filter.split(":=")[-1] if ":=" in meta.external_filter else "item"

            # Inject catalog type hint so extensions know which collection they're populating
            for item in raw_items:
                item.setdefault("_isp_type", type_label)

            ext_records = await extension_service.list_index_extensions(idx_id)
            raw_items = await apply_extensions(raw_items, ext_records)

            catalog_docs = [_generic_catalog_mapper(item, i) for i, item in enumerate(raw_items)]
            catalog_docs = [d for d in catalog_docs if d]

            # Embed on display + description for semantic search
            texts = [
                d["display"] + " " + json.loads(d.get("metadata", "{}")).get("description", "")
                for d in catalog_docs
            ]
            vectors = await embedding_service.embed_texts(texts)
            for doc, vec in zip(catalog_docs, vectors):
                if vec:
                    doc["vec"] = vec

            ok = ts.upsert_to_collection(meta.external_collection, catalog_docs)
            s.indexed = ok
            s.state = "done"

            meta.doc_count = ok
            meta.connectors = [{"type": "rest", **cfg_dict}]
            await index_service.update_index(meta)

        else:
            # ── Standard mode: write to ISP-managed collection ──
            docs = await rest_connector.fetch_and_parse(cfg)
            if not docs:
                raise ValueError("0 documents parsed — check field mapping.")

            ext_records = await extension_service.list_index_extensions(idx_id)
            docs = await apply_extensions(docs, ext_records)

            texts = [f"{d['title']} {d['content']}" for d in docs]
            vectors = await embedding_service.embed_texts(texts)
            for doc, vec in zip(docs, vectors):
                if vec:
                    doc["vec"] = vec

            ts.drop_collection(idx_id)
            ts.create_collection(idx_id)

            ok = ts.upsert_documents(idx_id, docs)
            s.indexed = ok
            s.state = "done"

            meta.doc_count = ok
            meta.is_external = False
            meta.collection_name = f"isp_{idx_id}"
            meta.connectors = [{"type": "rest", **cfg_dict}]
            await index_service.update_index(meta)

    except Exception as exc:
        s.state = "error"
        s.error = str(exc)

    await index_service.set_status(s)


@router.post("/{idx_id}/rebuild", response_model=IngestStatus, summary="Full rebuild (drop & re-index)")
async def rebuild(idx_id: str, cfg: RebuildRequest, bg: BackgroundTasks):
    """
    **Drop** all existing documents for this source and re-fetch everything from scratch.
    For catalog write-back sources (Phones, Accessories, Smartwatches, Tablets) this maps
    raw T-Mobile API items to the unified catalog schema and upserts into the shared
    `catalog` Typesense collection.
    Returns immediately with `state=queued`; poll `/status` to track progress.
    """
    meta = await index_service.get_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    cfg_dict = {"type": "rest", **cfg.model_dump()}
    bg.add_task(_run_rebuild, idx_id, cfg_dict)
    from isp.models import IngestStatus as IS
    s = IS(index_id=idx_id, state="queued")
    await index_service.set_status(s)
    return s


# ── Documents ─────────────────────────────────────────────────────────────────

@router.get("/{idx_id}/documents", summary="Browse indexed documents")
async def list_documents(idx_id: str, page: int = 1, per_page: int = 20):
    """
    Paginated list of documents in an index, sorted by recency.
    For external/catalog sources the results come from the shared Typesense collection
    filtered by this source's `external_filter`.
    """
    meta = await index_service.get_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")

    if meta.is_external:
        raw = ts.list_documents_external(
            meta.external_collection,
            meta.external_filter,
            meta.external_query_by,
            page, per_page,
        )
        return _wrap_external_results(raw, meta)

    return ts.list_documents(idx_id, page, per_page)
