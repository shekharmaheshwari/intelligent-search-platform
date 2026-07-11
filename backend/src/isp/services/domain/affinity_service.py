"""
Affinity dimension discovery for customer personalisation.

Scans a sample of indexed documents to identify low-cardinality categorical
fields suitable for affinity boosting.  Called automatically after every
successful rebuild and exposed via:

  GET  /v1/indexes/{id}/affinity-dimensions   — return current config
  PUT  /v1/indexes/{id}/affinity-dimensions   — save operator edits
  POST /v1/indexes/{id}/affinity-dimensions/discover — re-run discovery

The discovered dimensions replace the old hardcoded SearchContext fields
(category_affinity, form_factor_affinity, …) with a single generic
``affinities: dict[str, dict[str, float]]`` map.
"""
from __future__ import annotations
import json

# Maps a doc field name to the key used in CustomerAction dicts when they differ.
# Stamped onto discovered dimensions so context_service needs no hardcoded lookup.
_DOC_TO_ACTION_KEY: dict[str, str] = {
    "operating_system": "os_family",   # doc stores operating_system; action sends os_family
    "source_type":      "item_type",   # doc stores source_type;      action sends item_type
}

# Fields that are never affinity candidates regardless of cardinality
_SKIP_FIELDS = {
    "description", "images", "skus", "color_options", "url_name",
    "url", "title", "content", "id", "vec", "indexed_at", "popularity",
    "is_featured", "in_stock", "price",
    "display", "tags", "aliases", "metadata",
}

_MIN_DISTINCT = 2    # fields with fewer distinct values are too uniform to be useful
_MAX_DISTINCT = 50   # fields with more distinct values are too specific (free-text risk)
_SAMPLE_SIZE  = 250  # documents to scan during discovery
_PRICE_TIER_BOOST = 0.10  # fixed boost for the derived price_tier dimension


def _cardinality_boost(num_distinct: int) -> float:
    """
    Derive a boost weight from how many distinct values a field has.

    Fewer distinct values → stronger categorical signal → higher boost.
    Maps the range [2, 50] onto [0.20, 0.05] using an inverse-square-root curve,
    then rounds to 2 decimal places so UI values stay clean.

      2  values → 0.20   (e.g. category: handset / simcard)
      4  values → 0.14   (e.g. operating_system: android/ios/kaios/other)
      8  values → 0.10   (e.g. manufacturer with ~8 brands)
      20 values → 0.07
      50 values → 0.05
    """
    import math
    raw = 0.28 / math.sqrt(num_distinct)
    return round(max(0.05, min(0.20, raw)), 2)


def _safe_json(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _derive_price_tier(price) -> str | None:
    try:
        p = float(price)
        if p < 30:   return "budget"
        if p <= 80:  return "mid"
        return "premium"
    except (TypeError, ValueError):
        return None


async def discover(index_id: str) -> list[dict]:
    """
    Sample documents from an index and return a list of affinity dimension configs.

    Each entry:
      {
        "field":       str,       # metadata key or top-level doc field
        "label":       str,       # human-readable name for the UI
        "boost":       float,     # default additive score weight
        "enabled":     bool,      # whether this dimension is active
        "values":      list[str], # distinct values found during discovery
        "derived_from": str | None  # set to "price" for the price_tier dimension
      }

    Returns [] when no documents are found or the index doesn't exist.
    """
    from isp.services.infra import typesense_client as ts
    from isp.services.domain import index_service

    meta = await index_service.get_index(index_id)
    if not meta:
        return []

    # ── Sample documents ──────────────────────────────────────────────────────
    try:
        if meta.is_external:
            raw = ts.search_external(
                meta.external_collection,
                "",
                meta.external_filter,
                meta.external_query_by or "display",
                None,
                1, _SAMPLE_SIZE,
                exclude_vec=True,
            )
        else:
            raw = ts.search_documents(
                index_id, "", "lexical", 1, _SAMPLE_SIZE, exclude_vec=True,
            )
    except Exception:
        return []

    hits = raw.get("hits", [])
    if not hits:
        return []

    # ── Collect distinct string values per field ───────────────────────────────
    field_values: dict[str, set[str]] = {}
    has_numeric_price = False

    for hit in hits:
        doc = hit.get("document", {})

        # Top-level document fields (source_type, type, manufacturer, category…)
        for k, v in doc.items():
            if k in _SKIP_FIELDS or k.startswith("_"):
                continue
            if isinstance(v, str) and v.strip():
                field_values.setdefault(k, set()).add(v.lower().strip())

        # Metadata JSON blob
        m = _safe_json(doc.get("metadata"))
        for k, v in m.items():
            if k in _SKIP_FIELDS:
                if k == "price" and isinstance(v, (int, float)):
                    has_numeric_price = True
                continue
            if isinstance(v, str) and v.strip():
                field_values.setdefault(k, set()).add(v.lower().strip())
            elif isinstance(v, list):
                # e.g. compatible_devices: ["iphone-17-pro-max", "iphone-16"]
                for item in v:
                    if isinstance(item, str) and item.strip():
                        field_values.setdefault(k, set()).add(item.lower().strip())

    # ── Filter to cardinality range ───────────────────────────────────────────
    candidates = {
        field: sorted(vals)
        for field, vals in field_values.items()
        if _MIN_DISTINCT <= len(vals) <= _MAX_DISTINCT
    }

    # ── Build dimension list ──────────────────────────────────────────────────
    dims: list[dict] = []
    for field, values in candidates.items():
        dim: dict = {
            "field":        field,
            "label":        field.replace("_", " ").title(),
            "boost":        _cardinality_boost(len(values)),
            "enabled":      True,
            "values":       values,
            "derived_from": None,
        }
        if field in _DOC_TO_ACTION_KEY:
            dim["action_key"] = _DOC_TO_ACTION_KEY[field]
        dims.append(dim)

    # Add price_tier as a derived dimension when raw price data is present.
    # Its cardinality is always 3 (budget/mid/premium) so we use the fixed boost.
    if has_numeric_price and not any(d["field"] == "price_tier" for d in dims):
        dims.append({
            "field":        "price_tier",
            "label":        "Price Tier",
            "boost":        _PRICE_TIER_BOOST,
            "enabled":      True,
            "values":       ["budget", "mid", "premium"],
            "derived_from": "price",
        })

    # Sort by boost descending so the highest-signal dimensions appear first in the UI
    return sorted(dims, key=lambda d: -d["boost"])


async def discover_and_save(index_id: str) -> list[dict]:
    """Run discovery and persist the result to the indexes table."""
    from isp.services.domain import index_service
    dims = await discover(index_id)
    await index_service.set_affinity_dimensions(index_id, dims)
    return dims
