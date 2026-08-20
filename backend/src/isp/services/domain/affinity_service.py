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
import logging

log = logging.getLogger(__name__)

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
_PRICE_TIER_BOOST   = 0.10  # fixed boost for the derived price_tier dimension
_NUMERIC_TIER_BOOST = 0.10  # fixed boost for any other derived {field}_tier dimension


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
            log.debug("_safe_json: could not parse metadata JSON, treating as empty", exc_info=True)
            return {}
    return {}


def _percentile_split(values: list[float]) -> tuple[float, float] | None:
    """
    33rd / 66th percentile split of a numeric field's distinct sampled values.
    Shared by price_tier and any other derived {field}_tier dimension so both
    use the same per-index, data-derived cut points instead of a guessed
    constant. Returns None when there isn't enough distinct data (or the
    split degenerates) to produce a meaningful 3-way tier.
    """
    distinct = sorted(set(values))
    if len(distinct) < 3:
        return None

    def _percentile(pct: float) -> float:
        idx = pct * (len(distinct) - 1)
        lo = int(idx)
        hi = min(lo + 1, len(distinct) - 1)
        return round(distinct[lo] + (distinct[hi] - distinct[lo]) * (idx - lo), 2)

    lo_cut, hi_cut = _percentile(1 / 3), _percentile(2 / 3)
    return None if lo_cut >= hi_cut else (lo_cut, hi_cut)


def _price_tier_thresholds(prices: list[float]) -> dict:
    """
    Derive budget/mid/premium cut points from this index's own sampled prices
    instead of a fixed dollar amount — catalogs span very different price
    scales (e.g. accessories vs. phones), so a global <30 / <=80 split leaves
    some indexes with every item in one tier.

    Falls back to the old fixed 30/80 split when there isn't enough price
    variety in the sample to derive a meaningful split.
    """
    split = _percentile_split(prices)
    if split is None:
        return {"budget_max": 30.0, "mid_max": 80.0}
    budget_max, mid_max = split
    return {"budget_max": budget_max, "mid_max": mid_max}


def _numeric_tier_thresholds(values: list[float]) -> dict | None:
    """
    Derive low/mid/high cut points for an arbitrary numeric metadata field —
    e.g. a service catalog's sla_hours, a docs index's word_count — the same
    percentile approach as price_tier, generalised beyond price so
    non-commerce catalogs (no price field at all) still get comparable
    tiering on whatever numeric field they actually have.

    Unlike price, there's no universal fallback split for an arbitrary
    field, so this returns None (caller skips the dimension) when the
    sample doesn't have enough variety to derive one.
    """
    split = _percentile_split(values)
    if split is None:
        return None
    low_max, mid_max = split
    return {"low_max": low_max, "mid_max": mid_max}


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
        "derived_from": str | None  # source numeric field this dimension's value
                                     # is computed from — "price" for price_tier,
                                     # or the field name itself for any other
                                     # generic {field}_tier dimension; None for
                                     # plain categorical dimensions
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
        # Feeds discover_and_save (called after every rebuild) — a silent
        # failure here means affinity discovery quietly produces nothing,
        # which later just looks like "no affinity data" for this index.
        log.error("affinity discover: sample fetch failed for index_id=%s", index_id, exc_info=True)
        return []

    hits = raw.get("hits", [])
    if not hits:
        return []

    # ── Collect distinct string values per field, and any numeric field ────────
    field_values: dict[str, set[str]] = {}
    numeric_values: dict[str, list[float]] = {}

    for hit in hits:
        doc = hit.get("document", {})

        # Top-level document fields (source_type, type, manufacturer, category…)
        for k, v in doc.items():
            if k in _SKIP_FIELDS or k.startswith("_"):
                continue
            if isinstance(v, str) and v.strip():
                field_values.setdefault(k, set()).add(v.lower().strip())

        # Metadata JSON blob — numeric fields are collected regardless of
        # commerce-specific naming (price is just the first/most common
        # example) so non-commerce catalogs (service SLAs, doc word counts…)
        # get the same tiering treatment via numeric_values below.
        m = _safe_json(doc.get("metadata"))
        for k, v in m.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if k not in _SKIP_FIELDS or k == "price":
                    numeric_values.setdefault(k, []).append(float(v))
                continue
            if k in _SKIP_FIELDS:
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
    # Thresholds are derived per-index from the sampled price distribution.
    prices = numeric_values.pop("price", [])
    if prices and not any(d["field"] == "price_tier" for d in dims):
        dims.append({
            "field":        "price_tier",
            "label":        "Price Tier",
            "boost":        _PRICE_TIER_BOOST,
            "enabled":      True,
            "values":       ["budget", "mid", "premium"],
            "derived_from": "price",
            "thresholds":   _price_tier_thresholds(prices),
        })

    # Add a generic {field}_tier dimension for every other numeric metadata
    # field with enough variety to split meaningfully — same mechanism as
    # price_tier, generalised for catalogs with no commerce-specific fields
    # at all (e.g. a service index's sla_hours, a docs index's word_count).
    for field, values in numeric_values.items():
        tier_field = f"{field}_tier"
        if any(d["field"] == tier_field for d in dims):
            continue
        thresholds = _numeric_tier_thresholds(values)
        if thresholds is None:
            continue
        dims.append({
            "field":        tier_field,
            "label":        f"{field.replace('_', ' ').title()} Tier",
            "boost":        _NUMERIC_TIER_BOOST,
            "enabled":      True,
            "values":       ["low", "mid", "high"],
            "derived_from": field,
            "thresholds":   thresholds,
        })

    # Sort by boost descending so the highest-signal dimensions appear first in the UI
    return sorted(dims, key=lambda d: -d["boost"])


async def discover_and_save(index_id: str) -> list[dict]:
    """Run discovery and persist the result to the indexes table."""
    from isp.services.domain import index_service
    dims = await discover(index_id)
    await index_service.set_affinity_dimensions(index_id, dims)
    return dims
