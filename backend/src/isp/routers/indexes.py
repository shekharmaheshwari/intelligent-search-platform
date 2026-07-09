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
import asyncio, hashlib, json, re, time
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel
from isp.models import (
    IndexCreate, IndexMeta, IngestStatus,
    RestConnectorConfig, SitemapConnectorConfig, GSheetConnectorConfig, ConnectorConfig,
)
from isp.services.domain import index_service, extension_service, suggestion_service
from isp.services.domain.settings_service import DIM_BY_MODEL, get_custom_config
from isp.services.infra import embedding_service, typesense_client as ts
from isp.services.infra import rest_connector, sitemap_connector, gsheet_connector
from isp.services.infra.extension_runner import apply_extensions

router = APIRouter(prefix="/indexes", tags=["indexes"])


async def _resolve_index(id_or_slug: str) -> IndexMeta | None:
    """
    Return the index identified by either a hex id or a human-readable slug.

    Slug lookup runs first because slugs are the common consumer-facing form.
    Hex id lookup is the fallback so existing integrations continue to work
    without any URL changes.
    """
    # Try slug first (human-readable form used by consumer apps).
    meta = await index_service.get_index_by_slug(id_or_slug)
    return meta or await index_service.get_index(id_or_slug)


def _version_score(title: str) -> float:
    """
    Extract a [0.0, 1.0] recency score from a product title.

    Strategy (first match wins):
    1. 4-digit year 2020-2029  →  (year - 2019) / 10   (2020=0.1 … 2029=1.0)
    2. Model number 1-99 after a letter + optional space/dash,
       not followed by a unit abbreviation (GB, W, K, mAh, …)  →  num / 100

    The score is used as a small additive search boost when ``version_boost``
    is enabled on an index, so newer product generations surface above older ones
    for otherwise equally relevant queries (e.g. iPhone 16 > iPhone 14).
    """
    if not title:
        return 0.0
    # Year takes priority: "Galaxy Watch 2024" → 0.5
    m = re.search(r'\b(202\d)\b', title)
    if m:
        return round((int(m.group(1)) - 2019) / 10.0, 4)
    # Model number: "iPhone 16" / "Galaxy S25" / "Pixel 9" / "AirPods Pro 2"
    # Skip numbers followed by unit suffixes (GB, TB, W, K, mAh, MHz, GHz, mm, MP, Hz, in, ft)
    _UNITS = re.compile(r'(?i)^\s*(?:GB|TB|K\b|mAh|W\b|MHz|GHz|mm\b|MP\b|Hz\b|in\b|ft\b)')
    for m in re.finditer(r'(?<=[A-Za-z])[\s-]?(\d{1,2})\b', title):
        n = int(m.group(1))
        if n < 1:
            continue
        if _UNITS.match(title[m.end():]):
            continue
        return round(n / 100.0, 4)  # 16 → 0.16, 25 → 0.25, 9 → 0.09
    return 0.0


def _infer_isp_type(cfg_dict: dict) -> str:
    """
    Derive a catalog type label from the connector body's ``familyCategory`` field.

    REST connectors may carry a JSON body with ``familyCategory`` set to
    ``ACCESSORIES``, ``CELLPHONES``, ``TABLETS``, ``SMARTWATCHES``, etc.
    This label is injected as ``_isp_type`` on each raw item so the
    Catalog Mapper extension knows whether to produce ``"accessory"`` or
    ``"device"`` documents.  Other sources fall back to ``"item"``.
    """
    body_str = cfg_dict.get("body", "") or ""
    if body_str:
        try:
            body = json.loads(body_str)
            family = str(body.get("familyCategory", "")).upper()
            if "ACCESSOR" in family:
                return "accessory"
            if family:  # CELLPHONES, TABLETS, SMARTWATCHES → device
                return "device"
        except Exception:
            pass
    return "item"


# Fields stored directly on the ISP Typesense schema — anything else is packed into metadata.
_ISP_FIELDS = {
    "id", "title", "content", "url", "source_type", "indexed_at", "metadata", "vec",
    "boost_queries_equal", "boost_queries_contains",
}
# Internal pipeline flags injected before extensions run — never surfaced to Typesense.
_INTERNAL_FIELDS = {"_isp_type"}


def _ensure_isp_schema(docs: list[dict]) -> list[dict]:
    """
    Re-normalise a list of docs to the ISP Typesense collection schema.

    Works for two input shapes:

    * **ISP-schema docs** (from ``fetch_and_parse`` or sitemap): already have
      ``title``, ``content``, ``source_type``, ``indexed_at``; extra fields are
      packed into the ``metadata`` JSON blob.
    * **Catalog-schema docs** (from catalog-path JS extensions): have ``display``,
      ``type``, ``images``, ``price``, ``manufacturer``, etc.  All ISP fields are
      recovered from whichever key the extension left the data under; every remaining
      field is packed into ``metadata`` so rich product data (images, price,
      manufacturer, is_featured …) is preserved for the document expand view.

    Always produces docs with only the declared ISP schema fields so Typesense never
    rejects a document due to undeclared fields.  The ``id`` field is coerced to a
    hex string (Typesense requires string ids).

    Docs with no recoverable title *and* no recoverable content are dropped.
    """
    now = int(time.time())
    result = []
    for doc in docs:
        # Parse pre-existing catalog metadata blob so we can fall back to it for
        # url/description and merge extension fields on top of it.
        meta_raw = doc.get("metadata")
        meta: dict = {}
        if meta_raw:
            try:
                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
            except Exception:
                pass

        title = str(doc.get("title") or doc.get("display") or doc.get("name") or "")

        # content: explicit field → metadata.description → description → alias of title
        content = str(doc.get("content") or "")
        if not content:
            content = str(meta.get("description") or doc.get("description") or "")

        if not title and not content:
            continue

        # url: explicit field → metadata.url → url_name slug
        url = str(doc.get("url") or "")
        if not url:
            url = str(meta.get("url") or meta.get("url_name") or doc.get("url_name") or "")

        # id must be a string for Typesense; hash integer ids to a stable hex string
        raw_id = doc.get("id")
        if raw_id is None:
            doc_id = hashlib.md5(title.encode()).hexdigest()[:16]
        elif isinstance(raw_id, int) or (isinstance(raw_id, str) and not raw_id.isalnum()):
            doc_id = hashlib.md5(str(raw_id).encode()).hexdigest()[:16]
        else:
            doc_id = str(raw_id)

        # Collect every field that isn't a declared ISP schema field or an internal
        # pipeline flag. Merge on top of any pre-existing metadata so extension output
        # (images, price, manufacturer, is_featured, aliases …) is preserved intact.
        extra = {k: v for k, v in doc.items()
                 if k not in _ISP_FIELDS and k not in _INTERNAL_FIELDS}
        combined_meta = {**meta, **extra}
        metadata_str: str | None = json.dumps(combined_meta) if combined_meta else None

        # Build a clean ISP-schema doc — only declared Typesense fields
        out: dict = {
            "id":          doc_id,
            "title":       title or content[:80],
            "content":     content or title,
            "url":         url,
            "source_type": str(doc.get("type") or doc.get("source_type") or "rest"),
            "indexed_at":  int(doc.get("indexed_at") or now),
        }
        if metadata_str:
            out["metadata"] = metadata_str
        # Promote editorial signals to top-level so Typesense can sort/boost on them
        pop = combined_meta.get("popularity") or doc.get("popularity")
        if pop is not None:
            try:
                out["popularity"] = int(pop)
            except (ValueError, TypeError):
                pass
        feat = combined_meta.get("is_featured")
        if feat is None:
            feat = doc.get("is_featured")
        if feat is not None:
            out["is_featured"] = bool(feat)
        # Version recency score — prefer extension-computed value; fall back to Python extraction.
        vs = float(doc.get("version_score") or 0) or _version_score(title or content[:80])
        if vs > 0:
            out["version_score"] = round(vs, 4)
        # Merchandising boost queries (stamped by merchandising_rules.js extension)
        for bq_field in ("boost_queries_equal", "boost_queries_contains"):
            val = combined_meta.get(bq_field) or doc.get(bq_field)
            if isinstance(val, list) and val:
                out[bq_field] = [str(v) for v in val if v]
        # Carry over a pre-computed vector if the doc already has one
        if "vec" in doc:
            out["vec"] = doc["vec"]
        # Promote scalar extra fields (e.g. from SitemapConnectorConfig.meta_fields)
        # to top-level so the Typesense wildcard field (.*) indexes them as filterable.
        for k, v in extra.items():
            if isinstance(v, (str, int, float, bool)) and k not in out:
                out[k] = v

        result.append(out)
    return result


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

    out: dict = {
        "id":           doc_id,
        "display":      display,
        "type":         str(item.get("type") or "device"),
        "aliases":      list(item.get("aliases") or []),
        "tags":         list(item.get("tags") or []),
        "segment":      ["all"],
        "popularity":   int(item.get("popularity") or 0),
        "is_featured":  bool(item.get("is_featured", False)),
        "created_at":   now,
        "updated_at":   now,
        "metadata":     json.dumps(metadata),
    }
    # Prefer extension-computed version_score; fall back to Python extraction.
    vs = float(item.get("version_score") or 0) or _version_score(display)
    if vs > 0:
        out["version_score"] = round(vs, 4)
    # Merchandising boost queries (stamped by merchandising_rules.js extension)
    for bq_field in ("boost_queries_equal", "boost_queries_contains"):
        val = item.get(bq_field)
        if isinstance(val, list) and val:
            out[bq_field] = [str(v) for v in val if v]
    return out


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
    # 2. Try to parse metadata JSON (used in catalog write-back path)
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
    extension (e.g. Catalog Mapper) if one exists in the library.
    """
    from isp.main import _CATALOG_EXT_NAME
    new_indexes = await index_service.discover_external_collections()
    if new_indexes:
        exts = await extension_service.list_extensions()
        tmo = next((e for e in exts if e["name"] == _CATALOG_EXT_NAME), None)
        if tmo:
            for idx in new_indexes:
                if idx.is_external:
                    await extension_service.assign_extension_if_new(idx.id, tmo["id"], run_order=0)
    return new_indexes


@router.post("", response_model=IndexMeta, status_code=status.HTTP_201_CREATED, summary="Create a new index")
async def create_index(body: IndexCreate):
    """
    Create a new ISP-managed index and provision its Typesense collection.

    When ``embed_model`` is provided the collection is created with the matching
    vector dimension; otherwise the global active dimension is used.
    The index starts empty; use a connector + ingest or rebuild to populate it.
    """
    meta = await index_service.create_index(body.name, body.description, embed_model=body.embed_model, intent_label=body.intent_label, slug=body.slug)

    # Resolve the vector dimension for this index's chosen model so the Typesense
    # collection schema matches the vectors that will be written during ingest.
    effective_model = body.embed_model or embedding_service.get_current_model()
    if effective_model == "custom":
        cfg = await get_custom_config()
        vec_dim = cfg["num_dim"] or ts.get_vec_dim()
    else:
        vec_dim = DIM_BY_MODEL.get(effective_model, ts.get_vec_dim())

    ts.create_collection(meta.id, vec_dim=vec_dim)
    return meta


@router.get("/{idx_id}", response_model=IndexMeta, summary="Get index metadata")
async def get_index(idx_id: str):
    """Return the full metadata record for a single index, including connector config and doc count."""
    meta = await _resolve_index(idx_id)
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
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    await index_service.delete_index(idx_id)
    if meta.external_collection:
        ts.delete_from_collection(meta.external_collection, meta.external_filter)
    else:
        ts.drop_collection(idx_id)


class EmbedModelUpdate(BaseModel):
    """Request body for setting a per-index embedding model override."""
    model_id: str


@router.put("/{idx_id}/embed-model", response_model=IndexMeta, summary="Set per-index embedding model")
async def set_embed_model(idx_id: str, body: EmbedModelUpdate):
    """
    Store an embedding model override on this index.

    All subsequent ingest and search operations on this index will use the
    specified model instead of the global default.  Set ``model_id`` to ``""``
    (empty string) to revert to the global default.

    Changing the model does NOT automatically rebuild the index.  The vector
    dimension of the new model must match the Typesense collection schema, or
    the index must be fully rebuilt before semantic search will work correctly.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    meta.embed_model = body.model_id
    await index_service.update_index(meta)
    return meta


class VersionBoostUpdate(BaseModel):
    """Request body for enabling / disabling the version recency boost."""
    enabled: bool


@router.put("/{idx_id}/version-boost", response_model=IndexMeta, summary="Toggle version recency boost")
async def set_version_boost(idx_id: str, body: VersionBoostUpdate):
    """
    Enable or disable the version recency boost for this index.

    When enabled, products with higher model/generation numbers in their title
    receive a small additive score bonus during search, so newer items (e.g. iPhone 16)
    surface above older ones (e.g. iPhone 14) when relevance is otherwise equal.

    The ``version_score`` per document is computed at ingest time from the product
    title and stored as a Typesense field.  Rebuild the index after enabling this
    flag if documents were ingested before the feature was added.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    await index_service.set_version_boost(idx_id, body.enabled)
    meta.version_boost = body.enabled
    return meta


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
    # Resolve slug before passing to add_connector, which expects a hex id.
    resolved = await _resolve_index(idx_id)
    if not resolved:
        raise HTTPException(404, "Index not found")
    meta = await index_service.add_connector(resolved.id, {"type": "rest", **cfg.model_dump()})
    if not meta:
        raise HTTPException(404, "Index not found")
    return meta


@router.post("/{idx_id}/connectors/sitemap", response_model=IndexMeta, summary="Save a sitemap connector config")
async def add_sitemap_connector(idx_id: str, cfg: SitemapConnectorConfig):
    """
    Persist a sitemap connector configuration for the index without triggering a crawl.
    Use this to pre-configure the source; call `/ingest/sitemap` to start crawling.
    """
    resolved = await _resolve_index(idx_id)
    if not resolved:
        raise HTTPException(404, "Index not found")
    meta = await index_service.get_index(resolved.id)
    if not meta:
        raise HTTPException(404, "Index not found")
    new_cfg = {"type": "sitemap", **cfg.model_dump()}
    meta.connectors = [c for c in meta.connectors if c.get("type") != "sitemap"]
    meta.connectors.append(new_cfg)
    await index_service.update_index(meta)
    return meta


@router.post("/{idx_id}/connectors/gsheet", response_model=IndexMeta, summary="Save a Google Sheets connector config")
async def add_gsheet_connector(idx_id: str, cfg: GSheetConnectorConfig):
    """
    Persist a Google Sheets connector configuration for the index without triggering an import.

    Only one ``gsheet`` connector is stored per index — adding a new config replaces
    the previous one.  Call ``/ingest/gsheet`` to start the actual data import.
    """
    resolved = await _resolve_index(idx_id)
    if not resolved:
        raise HTTPException(404, "Index not found")
    meta = await index_service.get_index(resolved.id)
    if not meta:
        raise HTTPException(404, "Index not found")
    new_cfg = {"type": "gsheet", **cfg.model_dump()}
    # Replace any existing gsheet connector while keeping connectors of other types.
    meta.connectors = [c for c in meta.connectors if c.get("type") != "gsheet"]
    meta.connectors.append(new_cfg)
    await index_service.update_index(meta)
    return meta


# ── Ingest ────────────────────────────────────────────────────────────────────

async def _run_ingest(idx_id: str, connector_cfg: dict) -> None:
    """
    Background task: fetch → apply extensions → embed → append to ISP collection.

    Acquires a distributed Redis lock before starting so concurrent ingest runs
    across multiple ISP instances are serialised.  Extension records are loaded
    from the database at run time so changes made via the Extensions UI take
    effect on the next ingest without a restart.
    """
    from isp.models import IngestStatus
    from isp.services.infra import job_lock

    token = await job_lock.acquire(idx_id)
    if token is None:
        s = IngestStatus(index_id=idx_id, state="error",
                         error="Ingest already running on another instance — try again shortly")
        await index_service.set_status(s)
        return

    hb = asyncio.create_task(job_lock.heartbeat(idx_id, token))
    s = IngestStatus(index_id=idx_id, state="running")
    await index_service.set_status(s)
    try:
        ctype = connector_cfg.get("type")
        cfg_data = {k: v for k, v in connector_cfg.items() if k != "type"}

        ext_records = await extension_service.list_index_extensions(idx_id)

        if ctype == "rest":
            cfg = RestConnectorConfig(**cfg_data)
            if ext_records:
                # Run extensions on raw API items so they have access to all
                # original fields (e.g. urlEncodedFamilyName, skus, manufacturer).
                # Then _ensure_isp_schema recovers title/url/content from whatever
                # schema the extension produced.
                raw = await rest_connector.fetch_raw_items(cfg)
                s.total = len(raw)
                await index_service.set_status(s)
                raw = await apply_extensions(raw, ext_records)
                docs = _ensure_isp_schema(raw)
            else:
                docs = await rest_connector.fetch_and_parse(cfg)
                s.total = len(docs)
                await index_service.set_status(s)
                docs = _ensure_isp_schema(docs)
        elif ctype == "sitemap":
            cfg = SitemapConnectorConfig(**cfg_data)
            docs = await sitemap_connector.crawl(cfg, on_progress=None, ext_records=ext_records)
            s.total = len(docs)
            await index_service.set_status(s)
            docs = await apply_extensions(docs, ext_records)
            docs = _ensure_isp_schema(docs)
        elif ctype == "gsheet":
            cfg = GSheetConnectorConfig(**cfg_data)
            docs = await gsheet_connector.fetch_and_parse(cfg)
            s.total = len(docs)
            await index_service.set_status(s)
            docs = await apply_extensions(docs, ext_records)
            docs = _ensure_isp_schema(docs)
        else:
            raise ValueError(f"Unknown connector type: {ctype}")

        # Ensure the Typesense collection exists (idempotent; required for first-ever ingest).
        ts.create_collection(idx_id)

        meta = await index_service.get_index(idx_id)
        # Use this index's dedicated model, or fall back to the global active model.
        effective_model = (meta.embed_model if meta else "") or embedding_service.get_current_model()

        # Only embed docs that have non-empty text; skip the rest to avoid
        # empty-string rejections from the embedding provider.
        raw_texts = [f"{d.get('title', '')} {d.get('content', '')}".strip() for d in docs]
        embed_idxs = [i for i, t in enumerate(raw_texts) if t]
        if embed_idxs:
            vecs = await embedding_service.embed_texts(
                [raw_texts[i] for i in embed_idxs],
                model=effective_model,
            )
            for list_pos, doc_idx in enumerate(embed_idxs):
                if vecs[list_pos]:
                    docs[doc_idx]["vec"] = vecs[list_pos]

        ok = ts.upsert_documents(idx_id, docs)
        s.indexed = ok
        s.state = "done"

        if meta:
            meta.doc_count = ts.count_documents(idx_id)
            meta.connectors = [connector_cfg]
            await index_service.update_index(meta)

    except Exception as exc:
        s.state = "error"
        s.error = str(exc)

    finally:
        hb.cancel()
        await job_lock.release(idx_id, token)

    await index_service.set_status(s)


@router.post("/{idx_id}/ingest/rest", response_model=IngestStatus, summary="Ingest from a REST endpoint")
async def ingest_rest(idx_id: str, cfg: RestConnectorConfig, bg: BackgroundTasks):
    """
    Fetch documents from a REST API, generate embeddings, and **append** them to the index.
    The connector config is persisted so the Rebuild panel can pre-populate it later.
    Returns immediately with `state=queued`; poll `/status` to track progress.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    existing = await index_service.get_status(meta.id)
    if existing and existing.state in ("running", "queued"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Ingest already in progress for this index")
    # Persist connector immediately so Rebuild panel pre-populates even before ingest finishes
    meta.connectors = [{"type": "rest", **cfg.model_dump()}]
    await index_service.update_index(meta)
    # Pass meta.id (the resolved hex id) so the background task always operates on hex.
    bg.add_task(_run_ingest, meta.id, {"type": "rest", **cfg.model_dump()})
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
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    existing = await index_service.get_status(meta.id)
    if existing and existing.state in ("running", "queued"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Ingest already in progress for this index")
    meta.connectors = [{"type": "sitemap", **cfg.model_dump()}]
    await index_service.update_index(meta)
    # Pass meta.id (the resolved hex id) so the background task always operates on hex.
    bg.add_task(_run_ingest, meta.id, {"type": "sitemap", **cfg.model_dump()})
    from isp.models import IngestStatus as IS
    s = IS(index_id=idx_id, state="queued")
    await index_service.set_status(s)
    return s


@router.post("/{idx_id}/ingest/gsheet", response_model=IngestStatus, summary="Ingest from a Google Sheet")
async def ingest_gsheet(idx_id: str, cfg: GSheetConnectorConfig, bg: BackgroundTasks):
    """
    Read rows from a Google Spreadsheet, generate embeddings, and **append** them to the index.

    Credentials are loaded server-side from ``GOOGLE_SERVICE_ACCOUNT_JSON`` — the
    service account must have read access to the spreadsheet.
    Returns immediately with ``state=queued``; poll ``/status`` to track progress.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    existing = await index_service.get_status(meta.id)
    if existing and existing.state in ("running", "queued"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Ingest already in progress for this index")
    # Persist the connector so the UI can pre-populate the form on next visit.
    meta.connectors = [{"type": "gsheet", **cfg.model_dump()}]
    await index_service.update_index(meta)
    # Pass meta.id (the resolved hex id) so the background task always operates on hex.
    bg.add_task(_run_ingest, meta.id, {"type": "gsheet", **cfg.model_dump()})
    from isp.models import IngestStatus as IS
    s = IS(index_id=idx_id, state="queued")
    await index_service.set_status(s)
    return s


# ── Rebuild ──────────────────────────────────────────────────────────────────

async def _run_rebuild(idx_id: str, cfg_dict: dict) -> None:
    """
    Background task for full rebuild.

    Acquires a distributed Redis lock before starting so concurrent rebuilds
    across multiple ISP instances are serialised per index.

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
    from isp.services.infra import job_lock

    token = await job_lock.acquire(idx_id)
    if token is None:
        s = IngestStatus(index_id=idx_id, state="error",
                         error="Rebuild already running on another instance — try again shortly")
        await index_service.set_status(s)
        return

    hb = asyncio.create_task(job_lock.heartbeat(idx_id, token))
    s = IngestStatus(index_id=idx_id, state="running")
    await index_service.set_status(s)
    try:
        meta = await index_service.get_index(idx_id)
        if not meta:
            raise ValueError("Index not found")

        ctype = cfg_dict.get("type", "rest")
        cfg_data = {k: v for k, v in cfg_dict.items() if k != "type"}

        if meta.external_collection:
            # ── Write-back mode: catalog sources are always REST ──
            rest_cfg = RestConnectorConfig(**cfg_data)
            raw_items = await rest_connector.fetch_raw_items(rest_cfg)
            if not raw_items:
                raise ValueError("0 items fetched — API returned no usable data.")

            s.total = len(raw_items)
            await index_service.set_status(s)

            type_label = meta.external_filter.split(":=")[-1] if ":=" in meta.external_filter else "item"
            for item in raw_items:
                item.setdefault("_isp_type", type_label)

            ext_records = await extension_service.list_index_extensions(idx_id)
            raw_items = await apply_extensions(raw_items, ext_records)

            catalog_docs = [_generic_catalog_mapper(item, i) for i, item in enumerate(raw_items)]
            catalog_docs = [d for d in catalog_docs if d]

            effective_model = meta.embed_model or embedding_service.get_current_model()

            texts = [
                d["display"] + " " + json.loads(d.get("metadata", "{}")).get("description", "")
                for d in catalog_docs
            ]
            vectors = await embedding_service.embed_texts(texts, model=effective_model)
            for doc, vec in zip(catalog_docs, vectors):
                if vec:
                    doc["vec"] = vec

            ok = ts.upsert_to_collection(meta.external_collection, catalog_docs)
            s.indexed = ok
            s.state = "done"

            meta.doc_count = ok
            meta.connectors = [cfg_dict]
            await index_service.update_index(meta)

        elif ctype == "sitemap":
            # ── ISP-managed sitemap path ──
            sitemap_cfg = SitemapConnectorConfig(**cfg_data)
            ext_records = await extension_service.list_index_extensions(idx_id)
            docs = await sitemap_connector.crawl(sitemap_cfg, on_progress=None, ext_records=ext_records)
            if not docs:
                raise ValueError("0 pages crawled — check the sitemap URL and filters.")

            s.total = len(docs)
            await index_service.set_status(s)

            docs = await apply_extensions(docs, ext_records)
            docs = _ensure_isp_schema(docs)

            effective_model = meta.embed_model or embedding_service.get_current_model()

            raw_texts = [f"{d.get('title', '')} {d.get('content', '')}".strip() for d in docs]
            embed_idxs = [i for i, t in enumerate(raw_texts) if t]
            if embed_idxs:
                vecs = await embedding_service.embed_texts(
                    [raw_texts[i] for i in embed_idxs],
                    model=effective_model,
                )
                for list_pos, doc_idx in enumerate(embed_idxs):
                    if vecs[list_pos]:
                        docs[doc_idx]["vec"] = vecs[list_pos]

            ts.drop_collection(idx_id)
            if effective_model == "custom":
                cfg_c = await get_custom_config()
                rebuild_dim = cfg_c["num_dim"] or ts.get_vec_dim()
            else:
                rebuild_dim = DIM_BY_MODEL.get(effective_model, ts.get_vec_dim())
            ts.create_collection(idx_id, vec_dim=rebuild_dim)

            ok = ts.upsert_documents(idx_id, docs)
            s.indexed = ok
            s.state = "done"

            meta.doc_count = ok
            meta.is_external = False
            meta.collection_name = f"isp_{idx_id}"
            meta.connectors = [cfg_dict]
            await index_service.update_index(meta)

        elif ctype == "gsheet":
            # ── ISP-managed Google Sheets path ──
            gsheet_cfg = GSheetConnectorConfig(**cfg_data)
            ext_records = await extension_service.list_index_extensions(idx_id)
            docs = await gsheet_connector.fetch_and_parse(gsheet_cfg)
            if not docs:
                raise ValueError("0 rows fetched — check the spreadsheet ID and sheet name.")

            s.total = len(docs)
            await index_service.set_status(s)

            docs = await apply_extensions(docs, ext_records)
            docs = _ensure_isp_schema(docs)

            effective_model = meta.embed_model or embedding_service.get_current_model()

            raw_texts = [f"{d.get('title', '')} {d.get('content', '')}".strip() for d in docs]
            embed_idxs = [i for i, t in enumerate(raw_texts) if t]
            if embed_idxs:
                vecs = await embedding_service.embed_texts(
                    [raw_texts[i] for i in embed_idxs],
                    model=effective_model,
                )
                for list_pos, doc_idx in enumerate(embed_idxs):
                    if vecs[list_pos]:
                        docs[doc_idx]["vec"] = vecs[list_pos]

            ts.drop_collection(idx_id)
            if effective_model == "custom":
                cfg_c = await get_custom_config()
                rebuild_dim = cfg_c["num_dim"] or ts.get_vec_dim()
            else:
                rebuild_dim = DIM_BY_MODEL.get(effective_model, ts.get_vec_dim())
            ts.create_collection(idx_id, vec_dim=rebuild_dim)

            ok = ts.upsert_documents(idx_id, docs)
            s.indexed = ok
            s.state = "done"

            meta.doc_count = ok
            meta.is_external = False
            meta.collection_name = f"isp_{idx_id}"
            meta.connectors = [cfg_dict]
            await index_service.update_index(meta)

        else:
            # ── ISP-managed REST path ──
            rest_cfg = RestConnectorConfig(**cfg_data)
            raw_items = await rest_connector.fetch_raw_items(rest_cfg)
            if not raw_items:
                raise ValueError("0 items fetched — API returned no usable data.")

            s.total = len(raw_items)
            await index_service.set_status(s)

            isp_type_label = _infer_isp_type(cfg_dict)
            for item in raw_items:
                item.setdefault("_isp_type", isp_type_label)

            ext_records = await extension_service.list_index_extensions(idx_id)
            if ext_records:
                items_for_ext = await apply_extensions(list(raw_items), ext_records)
                docs = _ensure_isp_schema(items_for_ext)
            else:
                docs = await rest_connector.fetch_and_parse(rest_cfg)
                docs = _ensure_isp_schema(docs)

            if not docs:
                raise ValueError("0 documents parsed — check field mapping.")

            effective_model = meta.embed_model or embedding_service.get_current_model()

            raw_texts = [f"{d.get('title', '')} {d.get('content', '')}".strip() for d in docs]
            embed_idxs = [i for i, t in enumerate(raw_texts) if t]
            if embed_idxs:
                vecs = await embedding_service.embed_texts(
                    [raw_texts[i] for i in embed_idxs],
                    model=effective_model,
                )
                for list_pos, doc_idx in enumerate(embed_idxs):
                    if vecs[list_pos]:
                        docs[doc_idx]["vec"] = vecs[list_pos]

            ts.drop_collection(idx_id)
            if effective_model == "custom":
                cfg_c = await get_custom_config()
                rebuild_dim = cfg_c["num_dim"] or ts.get_vec_dim()
            else:
                rebuild_dim = DIM_BY_MODEL.get(effective_model, ts.get_vec_dim())
            ts.create_collection(idx_id, vec_dim=rebuild_dim)

            ok = ts.upsert_documents(idx_id, docs)
            s.indexed = ok
            s.state = "done"

            meta.doc_count = ok
            meta.is_external = False
            meta.collection_name = f"isp_{idx_id}"
            meta.connectors = [cfg_dict]
            await index_service.update_index(meta)

        # Seed the search_suggestions collection from newly indexed documents.
        # Non-critical: a failure here must never abort a successful rebuild.
        try:
            await suggestion_service.seed_suggestions_from_index(idx_id)
        except Exception:
            pass

    except Exception as exc:
        s.state = "error"
        s.error = str(exc)

    finally:
        hb.cancel()
        await job_lock.release(idx_id, token)

    await index_service.set_status(s)


@router.post("/{idx_id}/rebuild", response_model=IngestStatus, summary="Full rebuild (drop & re-index)")
async def rebuild(idx_id: str, cfg: ConnectorConfig, bg: BackgroundTasks):
    """
    **Drop** all existing documents for this source and re-fetch everything from scratch.
    Accepts either a REST or sitemap connector config — the ``type`` discriminator field
    determines which connector is used.
    For catalog write-back sources (Phones, Accessories, Smartwatches, Tablets) this maps
    raw API items to the unified catalog schema and upserts into the shared
    `catalog` Typesense collection.
    Returns immediately with `state=queued`; poll `/status` to track progress.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    existing = await index_service.get_status(meta.id)
    if existing and existing.state in ("running", "queued"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Rebuild already in progress for this index")
    # type field is already on the model (Literal["rest"] or Literal["sitemap"])
    cfg_dict = cfg.model_dump()
    # Pass meta.id (the resolved hex id) so the background task always operates on hex.
    bg.add_task(_run_rebuild, meta.id, cfg_dict)
    from isp.models import IngestStatus as IS
    s = IS(index_id=idx_id, state="queued")
    await index_service.set_status(s)
    return s


# ── Documents ─────────────────────────────────────────────────────────────────

async def _list_docs(idx_id: str, page: int, per_page: int,
                    exclude_vec: bool = False) -> dict:
    """Shared listing logic used by both /documents and /documents/public."""
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")

    if meta.is_external:
        raw = ts.list_documents_external(
            meta.external_collection,
            meta.external_filter,
            meta.external_query_by,
            page, per_page,
            exclude_vec=exclude_vec,
        )
        return _wrap_external_results(raw, meta)

    # Use meta.id (hex) not idx_id — caller may have passed a slug.
    return ts.list_documents(meta.id, page, per_page, exclude_vec=exclude_vec)


@router.get("/{idx_id}/documents", summary="Browse indexed documents")
async def list_documents(idx_id: str, page: int = 1, per_page: int = 20):
    """
    Paginated list of documents in an index, sorted by recency.
    Returns full documents including the ``vec`` embedding field.
    Use ``/documents/public`` for external consumers.
    """
    return await _list_docs(idx_id, page, per_page, exclude_vec=False)


@router.get("/{idx_id}/documents/public", summary="Browse documents — no embeddings",
            tags=["public"])
async def list_documents_public(idx_id: str, page: int = 1, per_page: int = 20):
    """
    Paginated document list with the ``vec`` embedding field stripped.

    Intended for external applications that browse or display index content
    without needing the raw embedding vectors.  Reduces response payload by
    ~4 KB per document (1 024 floats × 4 bytes).
    """
    return await _list_docs(idx_id, page, per_page, exclude_vec=True)


class DocPatchRequest(BaseModel):
    """Payload for editing a document's editorial signals (score, featured flag, boost queries)."""
    popularity: int | None = None
    is_featured: bool | None = None
    boost_queries_equal: list[str] | None = None
    boost_queries_contains: list[str] | None = None


@router.patch("/{idx_id}/documents/{doc_id}", summary="Update document score / featured flag")
async def update_document(idx_id: str, doc_id: str, patch: DocPatchRequest):
    """
    Partially update ``popularity`` (search boost score) and ``is_featured`` on a
    document without touching the rest of its fields or re-embedding.

    For ISP-managed indexes both values are stored inside the ``metadata`` JSON blob,
    so this endpoint reads, patches, and re-writes that blob via a Typesense partial
    update (``action=update``).

    Returns ``{"ok": true}`` on success or raises 404 / 500 on failure.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")

    # collection_name is keyed on the hex id, not the slug.
    col = ts.collection_name(meta.id)
    doc = ts.retrieve_document(col, doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")

    meta_raw = doc.get("metadata") or "{}"
    try:
        meta_dict = json.loads(meta_raw)
    except Exception:
        meta_dict = {}

    if patch.popularity is not None:
        meta_dict["popularity"] = patch.popularity
    if patch.is_featured is not None:
        meta_dict["is_featured"] = patch.is_featured

    # Write metadata blob + top-level fields so Typesense can sort/boost by them.
    # boost_queries_* are top-level array fields (not in metadata) — Typesense needs
    # them at the document root so the ranker can read them from search results.
    update_fields: dict = {"metadata": json.dumps(meta_dict)}
    if patch.popularity is not None:
        update_fields["popularity"] = patch.popularity
    if patch.is_featured is not None:
        update_fields["is_featured"] = patch.is_featured
    if patch.boost_queries_equal is not None:
        update_fields["boost_queries_equal"] = [s for s in patch.boost_queries_equal if s]
    if patch.boost_queries_contains is not None:
        update_fields["boost_queries_contains"] = [s for s in patch.boost_queries_contains if s]

    ok = ts.update_document_fields(col, doc_id, update_fields)
    if not ok:
        raise HTTPException(500, "Typesense update failed")
    return {"ok": True}


# ── Per-index suggestion management ──────────────────────────────────────────


@router.get("/{idx_id}/suggestions", summary="List suggestions for this index's category")
async def list_index_suggestions(
    idx_id:   str,
    q:        str = Query(default=""),
    page:     int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
):
    """
    Return paginated suggestions scoped to this index's intent_label (category).

    Uses ``ts.list_suggestions_by_category`` so only entries whose ``category``
    field matches the index label are returned.  Resolves the index first so both
    slug and hex id forms work as ``idx_id``.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    # intent_label is the category that populates this index's suggestion scope.
    category = meta.intent_label if hasattr(meta, "intent_label") else None
    result = ts.list_suggestions_by_category(category, q, page, per_page)
    hits  = result.get("hits", [])
    found = result.get("found", 0)
    suggestions = []
    for h in hits:
        # list_suggestions_by_category returns flattened document dicts (no wrapper key).
        doc = h.get("document", h)
        suggestions.append({
            "id":         doc.get("id", ""),
            "query":      doc.get("query", ""),
            "popularity": doc.get("popularity", 0),
            "boost":      doc.get("boost", 0),
            "type":       doc.get("type", "catalog"),
            "category":   doc.get("category"),
            "boosted":    (doc.get("boost", 0) or 0) > 0,
        })
    return {"suggestions": suggestions, "total": found, "page": page, "per_page": per_page}


@router.post("/{idx_id}/suggestions", summary="Create a manual suggestion for this index")
async def create_index_suggestion(idx_id: str, body: dict):
    """
    Upsert a manually created suggestion entry scoped to this index's category.

    ``query`` is required.  ``boost`` defaults to 1 so the new entry surfaces
    above organic popularity-ranked suggestions by default.  The stable id is
    derived from the query + category slug so repeated POSTs are idempotent.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    # Prefer the index's intent_label; fall back to a caller-supplied category.
    category = (meta.intent_label if hasattr(meta, "intent_label") else None) or body.get("category")
    query = body.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    boost = int(body.get("boost", 1))

    def _slug(t: str) -> str:
        """Convert text to a URL-safe slug for use in stable suggestion ids."""
        s = re.sub(r"[ _]", "-", t.lower())
        s = re.sub(r"[^a-z0-9-]", "", s)
        s = re.sub(r"-{2,}", "-", s)
        return s.strip("-")

    # Produce a stable id so identical entries are merged, not duplicated.
    sid = f"manual:{_slug(query)}:{_slug(category or 'global')}"
    entry: dict = {"id": sid, "query": query.lower(), "popularity": 0, "boost": boost, "type": "query"}
    if category:
        entry["category"] = category
    ts.upsert_suggestions([entry])
    return {**entry, "boosted": boost > 0}


@router.put("/{idx_id}/suggestions/{suggestion_id}", summary="Update boost on a suggestion")
async def update_index_suggestion(idx_id: str, suggestion_id: str, body: dict):
    """
    Partially update the ``boost`` field on a suggestion without touching other fields.

    Returns 404 when the suggestion does not exist in the ``search_suggestions``
    collection.  ``idx_id`` is validated so the route is only reachable for known
    indexes, but the boost update itself is applied regardless of category.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    boost = int(body.get("boost", 0))
    ok = ts.update_suggestion_boost(suggestion_id, boost)
    if not ok:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"id": suggestion_id, "boost": boost, "boosted": boost > 0}


@router.delete("/{idx_id}/suggestions/{suggestion_id}", summary="Delete a suggestion")
async def delete_index_suggestion(idx_id: str, suggestion_id: str):
    """
    Hard-delete a suggestion from the ``search_suggestions`` collection.

    Returns 404 when the suggestion does not exist.  ``idx_id`` is validated so
    the route is only reachable for known indexes, but the delete is unconditional
    on category — an operator can remove any suggestion regardless of scope.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    ok = ts.delete_suggestion(suggestion_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"deleted": True}
