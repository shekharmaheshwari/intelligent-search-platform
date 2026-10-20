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
import asyncio, hashlib, json, logging, re, time
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel
from isp.models import (
    IndexCreate, IndexMeta, IngestStatus,
    RestConnectorConfig, SitemapConnectorConfig, GSheetConnectorConfig, JsonFileConnectorConfig, ConnectorConfig,
    IntentLabelUpdate, MappingConfig,
)
from isp.services.domain import index_service, extension_service, suggestion_service
from isp.services.domain.settings_service import DIM_BY_MODEL, get_custom_config
from isp.services.infra import embedding_service, typesense_client as ts
from isp.services.infra import rest_connector, sitemap_connector, gsheet_connector, json_file_connector
from isp.services.infra.extension_runner import apply_extensions
from isp.services.infra.agent.setup_executor import _flatten_objects, _repair_doc
from isp.services.infra.chat.service import CHAT_PERSONA_DEFAULTS
from isp.services.infra.chat.concept_resolution import (
    CONCEPT_FIELD_CANDIDATES as _CONCEPT_FIELD_CANDIDATES,
    SORT_CONCEPTS as _SORT_CONCEPTS,
    SORTABLE_TYPES as _SORTABLE_TYPES,
    FILTER_CONCEPT_VALUE_TYPES as _FILTER_CONCEPT_VALUE_TYPES,
    UNIT_CONCEPTS as _UNIT_CONCEPTS,
    NUMERIC_ARRAY_TYPES as _NUMERIC_ARRAY_TYPES,
)

router = APIRouter(prefix="/indexes", tags=["indexes"])
log = logging.getLogger(__name__)


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
            log.debug("Could not parse familyCategory from connector body", exc_info=True)
    return "item"


# Fields stored directly on the ISP Typesense schema — anything else is packed into metadata.
_ISP_FIELDS = {
    "id", "title", "content", "url", "source_type", "indexed_at", "metadata", "vec",
    "boost_queries_equal", "boost_queries_contains",
}
# Internal pipeline flags injected before extensions run — never surfaced to Typesense.
_INTERNAL_FIELDS = {"_isp_type"}

# Typesense accepts alphanumeric characters, hyphens, and underscores in document IDs.
_TS_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def _resolve_doc_id(raw_id, fallback: str) -> str:
    """
    Return a Typesense-safe document ID.

    If the extension (or data source) provides a clean string ID — alphanumeric,
    hyphens, underscores — it is used as-is so external systems (analytics, BI
    tools, personalisation pipelines) can reference documents by their native key
    without a mapping table.

    Integer IDs and IDs containing spaces or special characters are hashed to a
    stable 16-char hex string as a fallback.
    """
    if raw_id is None:
        return hashlib.md5(fallback.encode()).hexdigest()[:16]
    s = str(raw_id)
    if _TS_ID_RE.match(s):
        return s
    return hashlib.md5(s.encode()).hexdigest()[:16]


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
                log.debug("Could not parse pre-existing metadata JSON for doc id=%s", doc.get("id"), exc_info=True)

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

        doc_id = _resolve_doc_id(doc.get("id"), title)

        # Collect every field that isn't a declared ISP schema field or an internal
        # pipeline flag. Merge on top of any pre-existing metadata so extension output
        # (images, price, manufacturer, is_featured, aliases …) is preserved intact.
        extra = {k: v for k, v in doc.items()
                 if k not in _ISP_FIELDS and k not in _INTERNAL_FIELDS}
        combined_meta = {**meta, **extra}
        # Structured reasoning facts stay in metadata, avoiding per-domain schema fields.
        if "reasoningData" in combined_meta:
            from isp.services.domain.reasoning_data import validate_reasoning_data
            try:
                combined_meta["reasoningData"] = validate_reasoning_data(combined_meta["reasoningData"])
            except (ValueError, TypeError, OverflowError) as exc:
                log.warning("Invalid reasoningData omitted for doc %s: %s", doc_id, exc)
                combined_meta.pop("reasoningData", None)
            extra.pop("reasoningData", None)
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
        # Also promotes string-list fields (e.g. an extension-computed
        # compatible_devices array) the same way boost_queries_* already are above —
        # otherwise an array-valued extension field is silently dropped here, present
        # in `metadata` but invisible to the Fields tab, faceting, and search.
        for k, v in extra.items():
            if k in out:
                continue
            if isinstance(v, (str, int, float, bool)):
                out[k] = v
            elif isinstance(v, list) and all(isinstance(x, str) for x in v):
                out[k] = v
            # Numeric-list extension fields (e.g. eligible_lines/line_prices for a
            # multi-value "number of lines" facet) were previously dropped here —
            # only string lists were promoted — leaving them stranded, invisible,
            # inside `metadata` (present for the doc-expand view, unusable for
            # faceting). bool excluded: it's already caught by the scalar branch
            # above and Python's bool is technically an int subtype.
            elif isinstance(v, list) and v and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
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
    Package an extension-normalised flat dict into the shared catalog collection.

    The extension is the single source of truth for all field names and values.
    Every field the extension returns passes through as-is — the catalog collection's
    ``.*`` wildcard indexes them all.  Operators enable faceting via the ISP console;
    no field names are hardcoded here.

    Returns None for items with no displayable name so they are silently skipped.
    """
    display = str(item.get("display") or item.get("name") or item.get("title") or "")
    if not display:
        return None

    now = int(time.time())

    # Pass everything from the extension through; override structural fields.
    out = {k: v for k, v in item.items() if k not in _INTERNAL_FIELDS}
    if "reasoningData" in out:
        from isp.services.domain.reasoning_data import validate_reasoning_data
        raw_facts = out.pop("reasoningData")
        existing_meta = out.get("metadata") or {}
        if isinstance(existing_meta, str):
            existing_meta = json.loads(existing_meta)
        existing_meta = dict(existing_meta)
        try:
            existing_meta["reasoningData"] = validate_reasoning_data(raw_facts)
        except (ValueError, TypeError, OverflowError) as exc:
            log.warning("Invalid reasoningData omitted for catalog item %s: %s", idx, exc)
            existing_meta.pop("reasoningData", None)
        out["metadata"] = json.dumps(existing_meta)
    out["id"]         = _resolve_doc_id(item.get("id") or idx, display)
    out["display"]    = display
    out["created_at"] = now
    out["updated_at"] = now
    out.setdefault("type",       str(item.get("type") or "device"))
    out.setdefault("segment",    ["all"])
    out.setdefault("popularity", int(item.get("popularity") or 0))
    out.setdefault("is_featured", bool(item.get("is_featured", False)))

    vs = float(item.get("version_score") or 0) or _version_score(display)
    if vs > 0:
        out["version_score"] = round(vs, 4)
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
                log.debug("Could not parse metadata JSON for content extraction: doc id=%s",
                          raw.get("id"), exc_info=True)
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
            log.debug("Could not parse metadata JSON for url extraction: doc id=%s",
                      raw.get("id"), exc_info=True)
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


@router.put("/{idx_id}/intent-label", response_model=IndexMeta, summary="Set intent label")
async def update_intent_label(idx_id: str, body: IntentLabelUpdate):
    """
    Set or clear the intent label on an index.  No rebuild is required.

    The intent label scopes typeahead suggestions and participates in the tab-intent
    vote at query time.  Set to '' (empty string) to remove this index from intent
    detection and suggestion scoping entirely.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    # Delegate to the targeted SQL UPDATE; avoid a full update_index call for a
    # single-column change so concurrent ingest jobs cannot overwrite each other's
    # connector state changes.
    await index_service.set_intent_label(meta.id, body.intent_label)
    meta.intent_label = body.intent_label
    return meta


class AffinityDimensionUpdate(BaseModel):
    """Request body for updating the affinity dimension config for an index."""
    dimensions: list[dict]
    viewed_item_policy: dict | None = None


@router.get("/{idx_id}/affinity-dimensions", summary="Get affinity dimensions for an index")
async def get_affinity_dimensions(idx_id: str):
    """
    Return the current affinity dimension config for this index.

    Dimensions are discovered automatically after every rebuild.  Each entry
    describes one categorical metadata field and controls whether it contributes
    to personalisation scoring and with what boost weight.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    return {
        "dimensions": meta.affinity_dimensions,
        "viewed_item_policy": meta.viewed_item_policy,
    }


@router.put("/{idx_id}/affinity-dimensions", summary="Update affinity dimension config")
async def update_affinity_dimensions(idx_id: str, body: AffinityDimensionUpdate):
    """
    Persist an updated affinity dimension config for this index.

    Operators can toggle individual dimensions on/off, tune boost weights,
    and update the viewed item policy without triggering a rebuild.
    Changes take effect immediately.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    await index_service.set_affinity_dimensions(meta.id, body.dimensions)
    policy = body.viewed_item_policy
    if policy is not None:
        await index_service.set_viewed_item_policy(meta.id, policy)
    else:
        policy = meta.viewed_item_policy
    return {"dimensions": body.dimensions, "viewed_item_policy": policy}


_FIELD_SYSTEM = {"id", "vec", "indexed_at"}
_FIELD_ALWAYS_SEARCHABLE = {"title", "content", "aliases"}


def _build_embed_text(doc: dict) -> str:
    """Build the text to embed for a document.

    Uses chatContent (built by the JS extension at index time) when present —
    it already contains all structured, labelled attributes for this index type.
    Falls back to title + content for indexes without an extension assigned.
    """
    return " ".join(p for p in [
        doc.get("title", ""),
        doc.get("content", ""),
        doc.get("chatContent", ""),
    ] if p).strip()


class FieldUpdate(BaseModel):
    facet:       bool | None = None
    multi_value: bool | None = None
    searchable:  bool | None = None
    chat:        bool | None = None
    # Which superlative/filter concept (see chat/service.py's
    # _CONCEPT_FIELD_CANDIDATES) this field represents on this index, e.g.
    # "price" or "customer_eligibility" — an explicit operator declaration,
    # persisted to indexes.superlative_field_map, checked before the
    # heuristic name-match the chat runtime otherwise falls back on.
    # None = leave unchanged; "" = clear whatever concept this field has.
    concept:     str | None = None


# Concepts an operator can tag a field with from the Fields tab — the same
# table the chat runtime's superlative/filter resolution already uses, so a
# UI-set value and an auto-resolved one are indistinguishable to that code.
_KNOWN_SUPERLATIVE_CONCEPTS = set(_CONCEPT_FIELD_CANDIDATES.keys())


@router.get("/{idx_id}/fields", summary="Get all fields for an index")
async def get_index_fields(idx_id: str):
    """
    Return all Typesense fields for an ISP-managed index, merged with the stored
    field_config so the UI reflects both live schema state and operator settings.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    if meta.is_external:
        raise HTTPException(400, "Field configuration is not available for external indexes")
    schema_fields = ts.get_all_fields(meta.id)
    if not schema_fields:
        return {"fields": []}
    # Build a lookup from stored field_config
    cfg_by_name = {fc["name"]: fc for fc in (meta.field_config or [])}
    # Reverse the concept -> field_name map so each field row can show
    # "this field IS the price concept" directly, without the UI having to
    # know the map's shape.
    concept_by_field = {v: k for k, v in (meta.superlative_field_map or {}).items() if v}
    for f in schema_fields:
        stored = cfg_by_name.get(f["name"], {})
        f["searchable"] = stored.get("searchable", f["name"] in _FIELD_ALWAYS_SEARCHABLE)
        if f["name"] in _FIELD_ALWAYS_SEARCHABLE:
            f["searchable"] = True   # always on, cannot be disabled
        f["chat"] = stored.get("chat", False)
        f["concept"] = concept_by_field.get(f["name"], "")
    return {"fields": schema_fields}


@router.get("/{idx_id}/fields/public", summary="Get facet-enabled fields — no auth", tags=["public"])
async def get_index_fields_public(idx_id: str):
    """Return only the fields that have facet=true, for public consumers like the PLP."""
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    schema_fields = ts.get_all_fields(meta.id)
    if not schema_fields:
        return {"fields": []}
    cfg_by_name = {fc["name"]: fc for fc in (meta.field_config or [])}
    for f in schema_fields:
        stored = cfg_by_name.get(f["name"], {})
        f["searchable"] = stored.get("searchable", f["name"] in _FIELD_ALWAYS_SEARCHABLE)
        if f["name"] in _FIELD_ALWAYS_SEARCHABLE:
            f["searchable"] = True
    facet_fields = [f for f in schema_fields if f.get("facet")]
    return {"fields": facet_fields}


@router.patch("/{idx_id}/fields/{field_name}", summary="Update field properties")
async def update_index_field(idx_id: str, field_name: str, body: FieldUpdate):
    """
    Toggle facet, multi-value, searchable, or chat on a single field.

    Facet and multi-value changes apply immediately via a Typesense schema update
    (drop + re-add — data is preserved).  Searchable and chat are stored in
    field_config (PostgreSQL) and take effect on the next request without any
    schema change.  Chat controls which fields appear as labelled attributes in
    the RAG chat context — no field names are hardcoded in the chat service.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    if meta.is_external:
        raise HTTPException(400, "Field configuration is not available for external indexes")
    if field_name in _FIELD_SYSTEM:
        raise HTTPException(400, f"'{field_name}' is a system field and cannot be modified")
    if field_name in _FIELD_ALWAYS_SEARCHABLE and body.searchable is False:
        raise HTTPException(400, f"'{field_name}' is always searchable and cannot be disabled")
    if body.concept and body.concept not in _KNOWN_SUPERLATIVE_CONCEPTS:
        raise HTTPException(
            400,
            f"Unknown concept '{body.concept}' — must be one of "
            f"{sorted(_KNOWN_SUPERLATIVE_CONCEPTS)} or empty to clear",
        )

    # Get current schema state for the field
    schema_fields = ts.get_all_fields(meta.id)
    field_map = {f["name"]: f for f in schema_fields}
    if field_name not in field_map:
        raise HTTPException(404, f"Field '{field_name}' not found in schema")
    current = field_map[field_name]

    # Resolve new values, defaulting to current state
    new_facet       = body.facet       if body.facet       is not None else current["facet"]
    new_multi_value = body.multi_value if body.multi_value is not None else current["multi_value"]
    field_type = current.get("type", "string")
    if body.searchable and not field_type.startswith("string"):
        raise HTTPException(400, f"'{field_name}' is type '{field_type}' — only string fields can be searchable")

    # A concept mapping — whether this PATCH is the one setting it, or it was
    # already sitting on this field from an earlier PATCH — must still fit
    # the field's EFFECTIVE type/facet state after this PATCH, not just
    # whatever shape it happened to have before. Two distinct ways this can
    # go wrong without this check: (1) body.concept names a concept whose
    # required shape this field doesn't have (e.g. tagging a string field as
    # "price"), and (2) an unrelated PATCH that only flips facet/type on a
    # field some OTHER earlier request already mapped a filter concept onto
    # (device_compatibility, customer_eligibility) — chat would then build
    # an invalid Typesense filter, or attempt to sort by a field it can't
    # sort by, the next time it resolves that concept.
    if body.concept is not None:
        effective_concept = body.concept or None
    else:
        concept_by_field_pre = {f: c for c, f in (meta.superlative_field_map or {}).items() if f}
        effective_concept = concept_by_field_pre.get(field_name) or None
    if effective_concept in _SORT_CONCEPTS:
        if field_type not in _SORTABLE_TYPES:
            raise HTTPException(
                400,
                f"Concept '{effective_concept}' needs a numeric field "
                f"({sorted(_SORTABLE_TYPES)}) — '{field_name}' is type '{field_type}'",
            )
    elif effective_concept in _UNIT_CONCEPTS:
        if field_type not in _NUMERIC_ARRAY_TYPES:
            raise HTTPException(
                400,
                f"Concept '{effective_concept}' needs a numeric array field "
                f"({sorted(_NUMERIC_ARRAY_TYPES)}) — '{field_name}' is type '{field_type}'",
            )
    elif effective_concept is not None:
        if not new_facet or field_type not in _FILTER_CONCEPT_VALUE_TYPES:
            raise HTTPException(
                400,
                f"Concept '{effective_concept}' needs a faceted field of type "
                f"{sorted(_FILTER_CONCEPT_VALUE_TYPES)} — '{field_name}' is type "
                f"'{field_type}' with facet={new_facet}",
            )

    # ── All validation is above this line — everything below commits a write. ──

    # Apply schema change if facet or multi_value changed
    if body.facet is not None or body.multi_value is not None:
        try:
            ts.update_field_properties(
                meta.id, field_name,
                facet=new_facet,
                multi_value=new_multi_value,
                current_type=current["type"],
            )
        except Exception as e:
            raise HTTPException(500, f"Schema update failed: {e}")

    # Persist searchable + chat + any facet/multi_value change into field_config.
    existing = next((fc for fc in (meta.field_config or []) if fc.get("name") == field_name), {})
    cfg_list = [fc for fc in (meta.field_config or []) if fc.get("name") != field_name]
    entry: dict = {
        "name":        field_name,
        "type":        field_type,   # stored so search.py can guard without a schema round-trip
        "facet":       new_facet,
        "multi_value": new_multi_value,
        "searchable":  body.searchable if body.searchable is not None
                       else existing.get("searchable", field_name in _FIELD_ALWAYS_SEARCHABLE),
        "chat":        body.chat if body.chat is not None
                       else existing.get("chat", False),
    }
    cfg_list.append(entry)
    meta.field_config = cfg_list
    await index_service.update_index(meta)

    # Concept tagging — a separate column (superlative_field_map), so it's
    # written independently of the field_config save above. body.concept is
    # None when the request didn't touch it at all (leave as-is); "" clears
    # whatever concept this field currently has; anything else must be a
    # concept the chat runtime actually knows how to resolve (validated above,
    # before any write). set_concept_field does the remove-old/set-new swap
    # atomically in one UPDATE so two concurrent concept PATCHes can't lose
    # one of the two writes (see its docstring).
    if body.concept is not None:
        await index_service.set_concept_field(meta.id, field_name, body.concept)
        new_concept = body.concept
    else:
        concept_by_field = {f: c for c, f in (meta.superlative_field_map or {}).items() if f}
        new_concept = concept_by_field.get(field_name, "")

    # Return the refreshed field
    refreshed = ts.get_all_fields(meta.id)
    refreshed_map = {f["name"]: f for f in refreshed}
    result = refreshed_map.get(field_name, current)
    result["searchable"] = entry["searchable"]
    result["chat"]       = entry["chat"]
    result["concept"]    = new_concept
    return result


@router.get("/{idx_id}/boost-rules", summary="Get boost rules for an index")
async def get_boost_rules(idx_id: str):
    """Return the current boost rules config for this index."""
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    return {"rules": meta.boost_rules}


@router.put("/{idx_id}/boost-rules", summary="Save boost rules for an index")
async def update_boost_rules(idx_id: str, body: dict):
    """
    Persist an updated boost rules list for this index.

    Changes take effect immediately on the next search request — no rebuild required.
    Each rule: {"id": str, "label": str, "enabled": bool, "boost": float,
                "trigger": [{"field": str, "operator": str, "value": str}],
                "targets": [{"field": str, "operator": str, "value": str}]}
    trigger: ALL must match (AND). targets: ANY must match (OR). Empty targets = all docs.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    rules = body.get("rules", [])
    if not isinstance(rules, list):
        raise HTTPException(400, "rules must be a list")
    # Clamp boost values server-side and validate regex patterns in trigger + targets.
    import re as _re
    for rule in rules:
        rule["boost"] = min(float(rule.get("boost", 0.0)), 1.0)
        for section in ("trigger", "targets"):
            for c in rule.get(section, []):
                if c.get("operator") == "regex":
                    try:
                        _re.compile(c.get("value", ""))
                    except _re.error as exc:
                        raise HTTPException(400, f"Invalid regex in rule '{rule.get('label', '')}' ({section}): {exc}")
    await index_service.set_boost_rules(meta.id, rules)
    return {"rules": rules}


_CHAT_PERSONA_MAX_INSTRUCTIONS_LEN = 2000


@router.get("/{idx_id}/chat-persona", summary="Get chat persona config for an index")
async def get_chat_persona(idx_id: str):
    """
    Return the effective chat persona config for this index — stored overrides merged
    over the defaults, so unset keys always come back with their default value rather
    than being absent.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    return {**CHAT_PERSONA_DEFAULTS, **(meta.chat_persona_config or {})}


@router.put("/{idx_id}/chat-persona", summary="Save chat persona config for an index")
async def update_chat_persona(idx_id: str, body: dict):
    """
    Persist the chat persona config for this index. Takes effect on the next chat
    request — no rebuild required.

    Body: {"item_noun": str, "enable_comparison": bool, "additional_instructions": str}
    Any key may be omitted; omitted keys fall back to CHAT_PERSONA_DEFAULTS at request time.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")

    config: dict = {}
    if "item_noun" in body:
        item_noun = str(body["item_noun"]).strip()
        if not item_noun:
            raise HTTPException(400, "item_noun cannot be empty")
        config["item_noun"] = item_noun
    if "enable_comparison" in body:
        config["enable_comparison"] = bool(body["enable_comparison"])
    if "additional_instructions" in body:
        instructions = str(body["additional_instructions"])
        if len(instructions) > _CHAT_PERSONA_MAX_INSTRUCTIONS_LEN:
            raise HTTPException(
                400, f"additional_instructions must be under {_CHAT_PERSONA_MAX_INSTRUCTIONS_LEN} characters",
            )
        config["additional_instructions"] = instructions

    await index_service.set_chat_persona_config(meta.id, config)
    return {**CHAT_PERSONA_DEFAULTS, **config}


@router.get("/{idx_id}/boost-rules/fields", summary="Get available fields for boost rule criteria")
async def get_boost_rule_fields(idx_id: str):
    """
    Return the filtered list of schema field names available for boost rule criteria.

    Strips internal system fields (vec, indexed_at, metadata, etc.) so the
    dropdown only shows business-meaningful fields like title, manufacturer, category.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    if meta.is_external:
        raise HTTPException(400, "Boost rule field discovery is not available for external indexes")
    fields = ts.get_boost_rule_fields(meta.id)
    return {"fields": fields}


class SynonymUpdate(BaseModel):
    synonyms: list[str]
    root:     str | None = None


@router.get("/{idx_id}/synonyms", summary="List synonym sets for an index")
async def get_synonyms(idx_id: str):
    """
    Return all synonym sets defined on this index's Typesense collection.

    Unlike Fields, this works for external indexes too — synonyms are a pure
    Typesense-collection feature, not an ISP-managed concept.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    return {"synonyms": ts.list_synonyms(meta.id)}


@router.put("/{idx_id}/synonyms/{synonym_id}", summary="Create or update a synonym set")
async def put_synonym(idx_id: str, synonym_id: str, body: SynonymUpdate):
    """
    Create or update a synonym set. Applies immediately — no rebuild required.

    ``root`` unset: every word in ``synonyms`` is mutually interchangeable
    (e.g. wire = cable = cord). ``root`` set: searching the root also matches
    the synonyms, but not vice versa.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    if len(body.synonyms) < 2:
        raise HTTPException(400, "A synonym set needs at least 2 words")
    try:
        result = ts.upsert_synonym(meta.id, synonym_id, body.synonyms, body.root)
    except Exception as e:
        raise HTTPException(500, f"Synonym save failed: {e}")
    return result


@router.delete("/{idx_id}/synonyms/{synonym_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a synonym set")
async def delete_synonym_endpoint(idx_id: str, synonym_id: str):
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    ts.delete_synonym(meta.id, synonym_id)


@router.post("/{idx_id}/affinity-dimensions/discover", summary="Re-run affinity dimension discovery")
async def rediscover_affinity_dimensions(idx_id: str):
    """
    Re-scan the index's documents and regenerate the affinity dimension config.

    Useful after the catalog changes significantly without a full rebuild, or
    when the operator wants to reset manual edits to the discovered defaults.
    """
    from isp.services.domain import affinity_service
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    dims = await affinity_service.discover_and_save(meta.id)
    meta = await _resolve_index(idx_id)
    return {"dimensions": dims, "viewed_item_policy": meta.viewed_item_policy}


@router.put("/{idx_id}/connectors/mapping", response_model=IndexMeta, summary="Save field renames and value overrides")
async def save_connector_mapping(idx_id: str, body: MappingConfig):
    """
    Persist field renames (field_map), value overrides (value_map), and static key-value
    pairs (static_fields) onto the index's connector config without triggering a rebuild.

    ``field_map`` and ``value_map`` are only applied to REST connectors; ``static_fields``
    is applied to every connector type.  All values are written as-is into the connector
    JSONB — sending empty dicts clears previously saved entries.

    Raises 404 if the index has no connector configured yet.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")

    if not meta.connectors:
        raise HTTPException(404, "No connector configured on this index — add one first")

    # Patch every connector in-place: REST connectors receive field_map and value_map;
    # all connector types receive static_fields so constant values can be stamped regardless
    # of connector type. All other fields on each connector are preserved unchanged.
    for i, conn in enumerate(meta.connectors):
        cfg_dict = dict(conn) if not isinstance(conn, dict) else conn
        ctype = cfg_dict.get("type", "")
        new_cfg = {**cfg_dict}
        if ctype == "rest":
            # field_map and value_map are REST-specific — sitemap/gsheet rename at the
            # column/header level inside their own connector logic, not via these maps.
            new_cfg["field_map"] = body.field_map
            new_cfg["value_map"] = body.value_map
        # static_fields is connector-agnostic: constant pairs override any same-named field.
        new_cfg["static_fields"] = body.static_fields
        meta.connectors[i] = new_cfg

    await index_service.update_index(meta)
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
        pre_proc_exts  = [e for e in ext_records if e.get("type") == "pre_processor"]
        transform_exts = [e for e in ext_records if e.get("type", "transform") == "transform"]

        if ctype == "rest":
            cfg = RestConnectorConfig(**cfg_data)
            if transform_exts:
                # Run extensions on raw API items so they have access to all
                # original fields (e.g. urlEncodedFamilyName, skus, manufacturer).
                # Then _ensure_isp_schema recovers title/url/content from whatever
                # schema the extension produced.
                raw = await rest_connector.fetch_raw_items(cfg, pre_processor_exts=pre_proc_exts)
                s.total = len(raw)
                await index_service.set_status(s)
                raw = await apply_extensions(raw, transform_exts)
                # Stamp static_fields onto every item after extensions so static values win.
                # **static_fields spreads last, overriding any same-named extension output.
                static_fields = connector_cfg.get("static_fields", {})
                if static_fields:
                    raw = [{**item, **static_fields} for item in raw if isinstance(item, dict)]
                docs = _ensure_isp_schema(raw)
            else:
                docs = await rest_connector.fetch_and_parse(cfg, pre_processor_exts=pre_proc_exts)
                s.total = len(docs)
                await index_service.set_status(s)
                # Stamp static_fields after field_map/value_map (already applied inside fetch_and_parse).
                static_fields = connector_cfg.get("static_fields", {})
                if static_fields:
                    docs = [{**item, **static_fields} for item in docs if isinstance(item, dict)]
                docs = _ensure_isp_schema(docs)
        elif ctype == "sitemap":
            cfg = SitemapConnectorConfig(**cfg_data)
            docs = await sitemap_connector.crawl(cfg, on_progress=None, ext_records=ext_records)
            s.total = len(docs)
            await index_service.set_status(s)
            docs = await apply_extensions(docs, transform_exts)
            # Stamp static_fields after the crawled page data and extensions have been processed.
            static_fields = connector_cfg.get("static_fields", {})
            if static_fields:
                docs = [{**item, **static_fields} for item in docs if isinstance(item, dict)]
            docs = _ensure_isp_schema(docs)
        elif ctype == "gsheet":
            cfg = GSheetConnectorConfig(**cfg_data)
            docs = await gsheet_connector.fetch_and_parse(cfg)
            s.total = len(docs)
            await index_service.set_status(s)
            docs = await apply_extensions(docs, ext_records)
            # Stamp static_fields after sheet data and extensions have been processed.
            static_fields = connector_cfg.get("static_fields", {})
            if static_fields:
                docs = [{**item, **static_fields} for item in docs if isinstance(item, dict)]
            docs = _ensure_isp_schema(docs)
        elif ctype == "json_file":
            cfg = JsonFileConnectorConfig(**cfg_data)
            raw = await json_file_connector.fetch_raw_items(cfg)
            s.total = len(raw)
            await index_service.set_status(s)
            if transform_exts:
                processed = await apply_extensions(list(raw), transform_exts)
                static_fields = connector_cfg.get("static_fields", {})
                if static_fields:
                    processed = [{**item, **static_fields} for item in processed if isinstance(item, dict)]
                docs = _ensure_isp_schema(processed)
                docs = [_repair_doc(docs[i], raw[i] if i < len(raw) else {})
                        for i in range(len(docs))]
            else:
                static_fields = connector_cfg.get("static_fields", {})
                if static_fields:
                    raw = [{**item, **static_fields} for item in raw if isinstance(item, dict)]
                docs = _ensure_isp_schema(raw)
                docs = [_flatten_objects(d) for d in docs]
        else:
            raise ValueError(f"Unknown connector type: {ctype}")

        # Ensure the Typesense collection exists (idempotent; required for first-ever ingest).
        ts.create_collection(idx_id)

        meta = await index_service.get_index(idx_id)
        # Use this index's dedicated model, or fall back to the global active model.
        effective_model = (meta.embed_model if meta else "") or embedding_service.get_current_model()

        # Only embed docs that have non-empty text; skip the rest to avoid
        # empty-string rejections from the embedding provider.
        raw_texts = [_build_embed_text(d) for d in docs]
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
        # Only visible to a client if it happens to poll ingest status — log
        # here too so a failure isn't invisible in server-side monitoring.
        log.error("Ingest failed: index_id=%s connector_type=%s",
                  idx_id, connector_cfg.get("type"), exc_info=True)

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
    # Persist connector immediately so Rebuild panel pre-populates even before ingest finishes.
    # Merge mapping fields from the stored connector so static_fields/field_map/value_map
    # set via the Mapping tab are never overwritten by the incoming request defaults.
    existing_conn = meta.connectors[0] if meta.connectors else {}
    new_cfg = {"type": "rest", **cfg.model_dump()}
    for _f in ("static_fields", "field_map", "value_map"):
        if _f in existing_conn:
            new_cfg[_f] = existing_conn[_f]
    meta.connectors = [new_cfg]
    await index_service.update_index(meta)
    # Pass meta.id (the resolved hex id) so the background task always operates on hex.
    bg.add_task(_run_ingest, meta.id, new_cfg)
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
    existing_conn = meta.connectors[0] if meta.connectors else {}
    new_cfg = {"type": "sitemap", **cfg.model_dump()}
    for _f in ("static_fields", "field_map", "value_map"):
        if _f in existing_conn:
            new_cfg[_f] = existing_conn[_f]
    meta.connectors = [new_cfg]
    await index_service.update_index(meta)
    # Pass meta.id (the resolved hex id) so the background task always operates on hex.
    bg.add_task(_run_ingest, meta.id, new_cfg)
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
    existing_conn = meta.connectors[0] if meta.connectors else {}
    new_cfg = {"type": "gsheet", **cfg.model_dump()}
    for _f in ("static_fields", "field_map", "value_map"):
        if _f in existing_conn:
            new_cfg[_f] = existing_conn[_f]
    meta.connectors = [new_cfg]
    await index_service.update_index(meta)
    # Pass meta.id (the resolved hex id) so the background task always operates on hex.
    bg.add_task(_run_ingest, meta.id, new_cfg)
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

        # Start from the full saved connector config so fields set outside the
        # rebuild form (result_path, id_field, title_field, content_field,
        # url_field, static_fields, field_map, value_map) are preserved.
        # The rebuild form only sends url/method/headers/body — overlay those.
        # Mapping fields (static_fields, field_map, value_map) are restored from
        # the stored connector AFTER the merge because model_dump() always includes
        # them as empty dicts, which would otherwise overwrite saved values.
        _existing = meta.connectors[0] if meta.connectors else {}
        _merged = dict(_existing)
        _merged.update(cfg_dict)
        for _f in ("static_fields", "field_map", "value_map"):
            if _f in _existing:
                _merged[_f] = _existing[_f]
        cfg_dict = _merged

        # Persist the connector config immediately so headers/URL are saved
        # even if the data fetch fails (e.g. 401, network error).
        meta.connectors = [cfg_dict]
        await index_service.update_index(meta)

        ctype = cfg_dict.get("type", "rest")
        cfg_data = {k: v for k, v in cfg_dict.items() if k != "type"}

        if meta.external_collection:
            # ── Write-back mode: catalog sources are always REST ──
            rest_cfg = RestConnectorConfig(**cfg_data)
            ext_records = await extension_service.list_index_extensions(idx_id)
            pre_proc_exts  = [e for e in ext_records if e.get("type") == "pre_processor"]
            transform_exts = [e for e in ext_records if e.get("type", "transform") == "transform"]
            raw_items = await rest_connector.fetch_raw_items(rest_cfg, pre_processor_exts=pre_proc_exts)
            if not raw_items:
                raise ValueError("0 items fetched — API returned no usable data.")

            s.total = len(raw_items)
            await index_service.set_status(s)

            type_label = meta.external_filter.split(":=")[-1] if ":=" in meta.external_filter else "item"
            for item in raw_items:
                item.setdefault("_isp_type", type_label)

            raw_items = await apply_extensions(raw_items, transform_exts)

            catalog_docs = [_generic_catalog_mapper(item, i) for i, item in enumerate(raw_items)]
            catalog_docs = [d for d in catalog_docs if d]

            effective_model = meta.embed_model or embedding_service.get_current_model()

            texts = [
                d["display"] + " " + str(d.get("content") or d.get("description") or "")
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
            _sf = cfg_dict.get("static_fields", {})
            if _sf:
                docs = [{**d, **_sf} for d in docs if isinstance(d, dict)]
            docs = _ensure_isp_schema(docs)

            effective_model = meta.embed_model or embedding_service.get_current_model()

            raw_texts = [_build_embed_text(d) for d in docs]
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
            _sf = cfg_dict.get("static_fields", {})
            if _sf:
                docs = [{**d, **_sf} for d in docs if isinstance(d, dict)]
            docs = _ensure_isp_schema(docs)

            effective_model = meta.embed_model or embedding_service.get_current_model()

            raw_texts = [_build_embed_text(d) for d in docs]
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

        elif ctype == "json_file":
            # ── ISP-managed JSON file path ──
            jf_cfg = JsonFileConnectorConfig(**cfg_data)
            ext_records = await extension_service.list_index_extensions(idx_id)
            transform_exts = [e for e in ext_records if e.get("type", "transform") == "transform"]
            raw_items = await json_file_connector.fetch_raw_items(jf_cfg)
            if not raw_items:
                raise ValueError("0 items read — check the file path and root_key.")

            s.total = len(raw_items)
            await index_service.set_status(s)

            _sf = cfg_dict.get("static_fields", {})
            if transform_exts:
                items_for_ext = await apply_extensions(list(raw_items), transform_exts)
                if _sf:
                    items_for_ext = [{**d, **_sf} for d in items_for_ext if isinstance(d, dict)]
                docs = _ensure_isp_schema(items_for_ext)
                docs = [_repair_doc(docs[i], raw_items[i] if i < len(raw_items) else {})
                        for i in range(len(docs))]
            else:
                if _sf:
                    raw_items = [{**d, **_sf} for d in raw_items if isinstance(d, dict)]
                docs = _ensure_isp_schema(raw_items)
                docs = [_flatten_objects(d) for d in docs]

            if not docs:
                raise ValueError("0 documents parsed — check field mapping.")

            effective_model = meta.embed_model or embedding_service.get_current_model()
            raw_texts = [_build_embed_text(d) for d in docs]
            embed_idxs = [i for i, t in enumerate(raw_texts) if t]
            if embed_idxs:
                vecs = await embedding_service.embed_texts(
                    [raw_texts[i] for i in embed_idxs], model=effective_model)
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
            ext_records = await extension_service.list_index_extensions(idx_id)
            pre_proc_exts  = [e for e in ext_records if e.get("type") == "pre_processor"]
            transform_exts = [e for e in ext_records if e.get("type", "transform") == "transform"]
            raw_items = await rest_connector.fetch_raw_items(rest_cfg, pre_processor_exts=pre_proc_exts)
            if not raw_items:
                raise ValueError("0 items fetched — API returned no usable data.")

            s.total = len(raw_items)
            await index_service.set_status(s)

            isp_type_label = _infer_isp_type(cfg_dict)
            for item in raw_items:
                item.setdefault("_isp_type", isp_type_label)

            _sf = cfg_dict.get("static_fields", {})
            if transform_exts:
                items_for_ext = await apply_extensions(list(raw_items), transform_exts)
                if _sf:
                    items_for_ext = [{**d, **_sf} for d in items_for_ext if isinstance(d, dict)]
                docs = _ensure_isp_schema(items_for_ext)
            else:
                docs = await rest_connector.fetch_and_parse(rest_cfg, pre_processor_exts=pre_proc_exts)
                if _sf:
                    docs = [{**d, **_sf} for d in docs if isinstance(d, dict)]
                docs = _ensure_isp_schema(docs)

            if not docs:
                raise ValueError("0 documents parsed — check field mapping.")

            effective_model = meta.embed_model or embedding_service.get_current_model()

            raw_texts = [_build_embed_text(d) for d in docs]
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

        # Re-apply operator field config after rebuild recreates the collection.
        # Non-critical: schema defaults are still usable if this step fails.
        if not meta.is_external and meta.field_config:
            try:
                schema_fields = ts.get_all_fields(meta.id)
                schema_map = {f["name"]: f for f in schema_fields}
                for fc in meta.field_config:
                    fname = fc.get("name", "")
                    if fname not in schema_map or fname in _FIELD_SYSTEM:
                        continue
                    current = schema_map[fname]
                    want_facet = fc.get("facet", current["facet"])
                    want_multi = fc.get("multi_value", current["multi_value"])
                    if want_facet != current["facet"] or want_multi != current["multi_value"]:
                        ts.update_field_properties(
                            meta.id, fname,
                            facet=want_facet,
                            multi_value=want_multi,
                            current_type=current["type"],
                        )
            except Exception:
                log.error("Post-rebuild field_config re-apply failed for index_id=%s — schema "
                          "defaults are in effect instead of operator settings", idx_id, exc_info=True)

        # Seed the search_suggestions collection from newly indexed documents.
        # Non-critical: a failure here must never abort a successful rebuild.
        try:
            await suggestion_service.seed_suggestions_from_index(idx_id)
        except Exception:
            log.error("Suggestion seeding failed after rebuild: index_id=%s", idx_id, exc_info=True)

        # Discover affinity dimensions from the newly indexed documents.
        # Non-critical: runs after suggestions so a failure here never blocks rebuild.
        try:
            from isp.services.domain import affinity_service
            await affinity_service.discover_and_save(idx_id)
        except Exception:
            log.error("Affinity dimension discovery failed after rebuild: index_id=%s", idx_id, exc_info=True)

    except Exception as exc:
        s.state = "error"
        s.error = str(exc)
        # Only visible to a client if it happens to poll rebuild status — log
        # here too so a failure isn't invisible in server-side monitoring.
        log.error("Rebuild failed: index_id=%s", idx_id, exc_info=True)

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
        # Silently discarding here means the write below replaces the doc's
        # entire metadata blob (images, price, manufacturer, ...) with just
        # the patch fields — a real data-loss risk, so log it loudly.
        log.error("update_document: existing metadata is not valid JSON — it will be "
                  "DROPPED and replaced by this patch: index_id=%s doc_id=%s",
                  idx_id, doc_id, exc_info=True)
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
            "id":              doc.get("id", ""),
            "query":           doc.get("query", ""),
            "popularity":      doc.get("popularity", 0),
            "boost":           doc.get("boost", 0),
            "type":            doc.get("type", "catalog"),
            "category":        doc.get("category"),
            "boosted":         (doc.get("boost", 0) or 0) > 0,
            # Link-mode fields — present only when set; None means "not configured".
            "mode":             doc.get("mode", "search"),
            "url":              doc.get("url"),
            "featured_doc_ids": doc.get("featured_doc_ids", []),
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


@router.put("/{idx_id}/suggestions/{suggestion_id}", summary="Update fields on a suggestion")
async def update_index_suggestion(idx_id: str, suggestion_id: str, body: dict):
    """
    Partially update one or more fields on a suggestion without touching omitted fields.

    Accepted fields: ``boost`` (int), ``mode`` (str), ``url`` (str|None),
    ``featured_doc_ids`` (list[str]).  At least one field must be present in the body.
    Returns 404 when the suggestion does not exist in the ``search_suggestions``
    collection.  ``idx_id`` is validated so the route is only reachable for known indexes.
    """
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")

    # Build the partial-update dict from whichever fields the caller supplied.
    fields: dict = {}
    if "boost" in body:
        fields["boost"] = int(body["boost"])
    if "mode" in body:
        fields["mode"] = str(body["mode"])
    # url may be explicitly cleared by sending null/empty string.
    if "url" in body:
        fields["url"] = str(body["url"]).strip() if body["url"] else None
    if "featured_doc_ids" in body:
        raw_ids = body["featured_doc_ids"]
        fields["featured_doc_ids"] = [str(i).strip() for i in raw_ids if str(i).strip()] if isinstance(raw_ids, list) else []

    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Typesense partial-update preserves omitted keys; exclude None string fields
    # (can't be stored), but keep empty lists (featured_doc_ids = [] clears the array).
    update_payload = {k: v for k, v in fields.items() if v is not None}

    ok = ts.update_suggestion(suggestion_id, update_payload)
    if not ok:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"id": suggestion_id, **fields, "boosted": (fields.get("boost", 0) or 0) > 0}


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
