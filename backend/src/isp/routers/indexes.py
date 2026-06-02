from __future__ import annotations
import asyncio, hashlib, json, time
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from isp.models import (
    IndexCreate, IndexMeta, IngestStatus,
    RestConnectorConfig, SitemapConnectorConfig,
)
from isp.services import index_service, embedding_service
from isp.services import typesense_client as ts
from isp.services import rest_connector, sitemap_connector

router = APIRouter(prefix="/indexes", tags=["indexes"])


# ── Catalog write-back mapper ─────────────────────────────────────────────────

def _map_api_item_to_catalog(item: dict, type_label: str, idx: int) -> dict | None:
    """Convert a raw API item into a catalog-schema document."""
    # Try to find a stable ID field
    raw_id = (
        item.get("familyCode") or item.get("sku") or item.get("id") or str(idx)
    )
    display = (
        item.get("familyName") or item.get("name") or item.get("title") or item.get("display") or ""
    )
    if not display:
        return None

    manufacturer = str(item.get("manufacturer") or item.get("brand") or "")
    os_name = str(item.get("operatingSystem") or "")
    url_name = str(item.get("urlEncodedFamilyName") or item.get("urlName") or "")
    rank = int(item.get("deviceRank") or item.get("rank") or 999)

    color_names = [c.get("name", "") for c in item.get("colorOptions", []) if isinstance(c, dict)]
    skus_raw = [s for s in item.get("skus", []) if isinstance(s, dict)]
    skus = [s.get("skuCode", "") for s in skus_raw if s.get("skuCode")]

    # Prefer high-res PNG images from skus[].images (FrontImage > LeftImage > any)
    images: list[str] = []
    for sku in skus_raw[:3]:
        for img in sku.get("images", []):
            if not isinstance(img, dict):
                continue
            url = img.get("url", "")
            itype = img.get("imgtype", "")
            if url and itype in ("FrontImage", "LeftImage", "ThumbNail"):
                images.append(url)
                break
    # Deduplicate while preserving order
    seen: set[str] = set()
    images = [u for u in images if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]

    aliases = []
    if manufacturer and display:
        aliases.append(f"{manufacturer} {display}".lower())

    tags = list({t for t in [manufacturer.lower(), os_name.lower(), "phone", "smartphone"] if t})

    metadata = {
        "manufacturer": manufacturer,
        "url_name": url_name,
        "images": images[:3],
        "color_options": color_names,
        "skus": skus[:5],
        "description": f"{display} by {manufacturer}." + (f" {os_name} smartphone." if os_name else ""),
        "operating_system": os_name,
    }

    doc_id = f"dev_{hashlib.md5(str(raw_id).encode()).hexdigest()[:12]}"
    now = int(time.time())

    return {
        "id": doc_id,
        "display": display,
        "type": type_label,
        "aliases": aliases,
        "tags": tags,
        "segment": ["all"],
        "popularity": max(0, 1000 - rank * 10),
        "is_featured": rank <= 5,
        "created_at": now,
        "updated_at": now,
        "metadata": json.dumps(metadata),
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

    # Build URL
    url = ""
    if meta.external_url_field:
        # Try to get url_name from metadata
        meta_raw = raw.get("metadata")
        url_name = ""
        if meta_raw:
            try:
                md = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                url_name = str(md.get("url_name") or "")
            except Exception:
                pass
        if url_name:
            url = meta.external_url_field.rstrip("/") + "/" + url_name
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
    hits = [{"document": _map_external_doc(h["document"], meta)} for h in raw.get("hits", [])]
    return {"hits": hits, "found": raw.get("found", 0), "page": raw.get("page", 1)}


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[IndexMeta])
async def list_indexes():
    return await index_service.list_indexes()


@router.post("/discover", response_model=list[IndexMeta])
async def discover_indexes():
    """Auto-import existing Typesense collections as read-only ISP sources."""
    return await index_service.discover_external_collections()


@router.post("", response_model=IndexMeta, status_code=status.HTTP_201_CREATED)
async def create_index(body: IndexCreate):
    meta = await index_service.create_index(body.name, body.description)
    ts.create_collection(meta.id)
    return meta


@router.get("/{idx_id}", response_model=IndexMeta)
async def get_index(idx_id: str):
    meta = await index_service.get_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    return meta


@router.delete("/{idx_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_index(idx_id: str):
    meta = await index_service.get_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    await index_service.delete_index(idx_id)
    if meta.external_collection:
        # Delete documents from the shared Typesense collection using the filter
        ts.delete_from_collection(meta.external_collection, meta.external_filter)
    else:
        # Drop the ISP-owned collection entirely
        ts.drop_collection(idx_id)


@router.get("/{idx_id}/status", response_model=IngestStatus)
async def get_status(idx_id: str):
    return await index_service.get_status(idx_id)


# ── Connectors ────────────────────────────────────────────────────────────────

@router.post("/{idx_id}/connectors/rest", response_model=IndexMeta)
async def add_rest_connector(idx_id: str, cfg: RestConnectorConfig):
    meta = await index_service.add_connector(idx_id, {"type": "rest", **cfg.model_dump()})
    if not meta:
        raise HTTPException(404, "Index not found")
    return meta


@router.post("/{idx_id}/connectors/sitemap", response_model=IndexMeta)
async def add_sitemap_connector(idx_id: str, cfg: SitemapConnectorConfig):
    meta = await index_service.add_connector(idx_id, {"type": "sitemap", **cfg.model_dump()})
    if not meta:
        raise HTTPException(404, "Index not found")
    return meta


# ── Ingest ────────────────────────────────────────────────────────────────────

async def _run_ingest(idx_id: str, connector_cfg: dict) -> None:
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


@router.post("/{idx_id}/ingest/rest", response_model=IngestStatus)
async def ingest_rest(idx_id: str, cfg: RestConnectorConfig, bg: BackgroundTasks):
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


@router.post("/{idx_id}/ingest/sitemap", response_model=IngestStatus)
async def ingest_sitemap(idx_id: str, cfg: SitemapConnectorConfig, bg: BackgroundTasks):
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
    """Fetch → embed → overwrite. Writes back to external collection when applicable."""
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
            # ── Write-back mode: map to catalog schema and upsert into catalog ──
            type_label = meta.external_filter.split(":=")[-1] if ":=" in meta.external_filter else "item"
            catalog_docs = [
                _map_api_item_to_catalog(item, type_label, i)
                for i, item in enumerate(raw_items)
            ]
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


@router.post("/{idx_id}/rebuild", response_model=IngestStatus)
async def rebuild(idx_id: str, cfg: RebuildRequest, bg: BackgroundTasks):
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

@router.get("/{idx_id}/documents")
async def list_documents(idx_id: str, page: int = 1, per_page: int = 20):
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
