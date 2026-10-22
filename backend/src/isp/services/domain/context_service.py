# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Customer action history → personalised search context.

Two main outputs:

1. ``SearchContext`` — generic affinity maps derived from action weights.
   ``affinities`` is a dict[field_name, dict[value, score]] so any set of
   categorical metadata fields can drive personalisation without code changes.
   Affinity dims come from each index's ``affinity_dimensions`` JSON column,
   discovered automatically after every rebuild.

2. Preference vector blending — embedding vectors of recently viewed items
   are fetched from Typesense and blended into the query vector before the
   semantic search pass, steering ANN retrieval toward products the customer
   actually engages with.

None of this is stateful: callers pass in the action list on every request.
"""
from __future__ import annotations
import json
import logging

log = logging.getLogger(__name__)
import math
from dataclasses import dataclass, field
from isp.services.domain import viewed_item_policy as vip

_ACTION_WEIGHT: dict[str, float] = {
    "purchase": 3.0,
    "cart":     2.0,
    "view":     1.0,
    "search":   0.5,
    "category": 0.3,
}

_VECTOR_ELIGIBLE = {"view", "cart", "purchase"}

# Empty — callers must supply affinity_dims explicitly (from the per-index DB column).
# Catalog search builds the union of all active index dims before calling build_context.
_DEFAULT_DIMS: list[dict] = []


@dataclass
class SearchContext:
    """Pre-computed affinity context derived from a customer's action history."""
    # Generic map: metadata field name → {value → normalised affinity score [0,1]}
    # e.g. {"category": {"phones": 0.8, "accessories": 0.2}, "form_factor": {"flip": 1.0}}
    affinities:      dict[str, dict[str, float]] = field(default_factory=dict)
    # Items viewed or carted but NOT (yet) purchased — active interest, need unfulfilled.
    viewed_ids:      set[str]                    = field(default_factory=set)
    # Items the customer completed a purchase for — need satisfied (durable goods).
    # Purchase supersedes view: an item in purchased_ids is removed from viewed_ids.
    purchased_ids:   set[str]                    = field(default_factory=set)
    # Ordered list of item ids whose vectors will seed preference blending.
    # Capped at 5 — retrieved synchronously from Typesense, so latency matters.
    vector_seed_ids: list[str]                   = field(default_factory=list)


def parse_meta(raw) -> dict:
    """Safely deserialise the ``metadata`` JSON string field from an ISP document."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            # A document's metadata is silently dropped here — the loss only
            # surfaces later as missing price/filter/affinity data in unrelated
            # search or chat results, with no trace of the actual parse failure.
            log.warning("parse_meta: could not parse document metadata JSON, treating as empty", exc_info=True)
            return {}
    return {}


def build_context(
    actions: list[dict],
    affinity_dims: list[dict] | None = None,
    max_actions: int = 50,
) -> SearchContext:
    """
    Convert a flat list of customer action dicts into a ``SearchContext``.

    ``affinity_dims`` — the index's affinity dimension config list (each entry is a
    dict with at least ``field``, ``enabled``, and optionally ``action_key``).
    Falls back to ``_DEFAULT_DIMS`` (empty list) when not supplied — no affinity
    boost applied.  Catalog search passes the union of all active index dims explicitly.

    ``action_key`` on a dimension overrides which key is read from the action dict.
    This handles mismatches between doc field names and PLP action dict keys
    (e.g. doc: ``operating_system`` / action: ``os_family``).

    Each action is weighted by type (purchase > cart > view > search > category)
    and an optional caller-supplied ``weight`` multiplier.  All affinity scores
    are normalised to [0, 1] so boost magnitudes remain predictable regardless
    of how many actions are passed.
    """
    dims = affinity_dims if affinity_dims is not None else _DEFAULT_DIMS
    active_dims = [d for d in dims if d.get("enabled", True)]
    ctx = SearchContext()
    raw: dict[str, dict[str, float]] = {d["field"]: {} for d in active_dims}

    for action in actions[:max_actions]:
        action_type = action.get("type", "view")
        base_w      = _ACTION_WEIGHT.get(action_type, 1.0)
        caller_w    = float(action.get("weight", 1.0))
        w           = base_w * max(caller_w, 0.0)

        for dim in active_dims:
            field_name = dim["field"]
            action_key = dim.get("action_key") or field_name
            val = action.get(action_key)
            if val and isinstance(val, str):
                k = val.lower().strip()
                if k:
                    raw[field_name][k] = raw[field_name].get(k, 0.0) + w

        if action.get("item_id") and action_type in _VECTOR_ELIGIBLE:
            iid = action["item_id"]
            if action_type == "purchase":
                ctx.purchased_ids.add(iid)
                ctx.viewed_ids.discard(iid)   # purchase supersedes view
            elif iid not in ctx.purchased_ids:
                ctx.viewed_ids.add(iid)
            if len(ctx.vector_seed_ids) < 5:
                ctx.vector_seed_ids.append(iid)

    def _norm(d: dict[str, float]) -> dict[str, float]:
        if not d:
            return {}
        mx = max(d.values())
        return {k: round(v / mx, 4) for k, v in d.items()} if mx > 0 else {}

    ctx.affinities = {d["field"]: _norm(raw[d["field"]]) for d in active_dims}
    return ctx


def context_boost(
    doc: dict,
    ctx: SearchContext,
    affinity_dims: list[dict] | None = None,
    apply_viewed_penalty: bool = True,
    viewed_item_policy: dict | None = None,
) -> float:
    """
    Compute the additive context score for a single document.

    ``affinity_dims`` — the index's affinity dimension config list, each entry:
      { "field": str, "boost": float, "enabled": bool, "derived_from": str|None }
      ``derived_from`` is "price" for the price_tier dimension, the source
      numeric field's own name for any other generic {field}_tier dimension
      (e.g. "sla_hours"), or None for a plain categorical dimension.

    ``viewed_item_policy`` — per-index config controlling how viewed items are scored:
      { "mode": "penalise"|"neutral"|"boost", "value": float }
      Falls back to the legacy -0.05 penalty when not supplied.

    When ``affinity_dims`` is None the function falls back to a default set of
    dimensions with preset weights (backward-compatible with the old hardcoded path).

    Boost magnitudes are kept small relative to editorial boosts
    (is_featured +0.5, popularity ≤ +0.5) so context nudges rather than
    overrides relevance.
    """
    meta  = parse_meta(doc.get("metadata"))
    boost = 0.0

    dims = affinity_dims if affinity_dims is not None else _default_dims()

    for dim in dims:
        if not dim.get("enabled", True):
            continue

        field_name = dim["field"]
        weight     = float(dim.get("boost", 0.10))
        affinity   = ctx.affinities.get(field_name, {})
        if not affinity:
            continue

        derived = dim.get("derived_from")

        if derived == "price":
            # price_tier is derived from the raw price stored in metadata.
            # Thresholds are per-index (set by affinity_service.discover from
            # that index's own price distribution) since catalogs span very
            # different price scales (e.g. accessories vs. phones) — falls
            # back to the old fixed 30/80 split for indexes discovered before
            # per-index thresholds existed.
            raw_price = meta.get("price")
            if raw_price is not None:
                try:
                    p = float(raw_price)
                    thresholds = dim.get("thresholds") or {}
                    budget_max = float(thresholds.get("budget_max", 30))
                    mid_max    = float(thresholds.get("mid_max", 80))
                    doc_val = "budget" if p < budget_max else ("mid" if p <= mid_max else "premium")
                except (TypeError, ValueError):
                    doc_val = None
            else:
                doc_val = None

        elif field_name == "type":
            doc_val = (doc.get("source_type") or meta.get("type") or "").lower() or None

        elif derived:
            # Generic {field}_tier dimension (e.g. sla_hours_tier,
            # word_count_tier) — same low/mid/high split as price_tier,
            # generalised to any numeric metadata field so non-commerce
            # catalogs (no "price" field at all) still get comparable
            # tiering. `derived` here is the source field's own name.
            # Unlike price_tier there's no universal fallback split, so a
            # dimension with no thresholds yet (not re-discovered since this
            # field was added) simply contributes no boost.
            raw_val = meta.get(derived)
            thresholds = dim.get("thresholds") or {}
            if raw_val is not None and thresholds:
                try:
                    n = float(raw_val)
                    low_max = float(thresholds.get("low_max", 0))
                    mid_max = float(thresholds.get("mid_max", 0))
                    doc_val = "low" if n < low_max else ("mid" if n <= mid_max else "high")
                except (TypeError, ValueError):
                    doc_val = None
            else:
                doc_val = None

        else:
            # Check top-level doc fields first, then metadata
            raw_val = doc.get(field_name) or meta.get(field_name)
            if isinstance(raw_val, list):
                # e.g. compatible_devices — any match counts
                for v in raw_val:
                    k = str(v).lower().strip()
                    if k and k in affinity:
                        boost += weight * affinity[k]
                        break
                continue
            doc_val = str(raw_val).lower().strip() if raw_val else None

        if doc_val and doc_val in affinity:
            boost += weight * affinity[doc_val]

    if apply_viewed_penalty:
        boost += vip.apply(doc.get("id", ""), ctx.viewed_ids, ctx.purchased_ids, viewed_item_policy)

    return boost


def _default_dims() -> list[dict]:
    """Fallback dimension list used by context_boost when no per-index config is supplied."""
    return _DEFAULT_DIMS


def blend_preference_vector(
    query_vec: list[float],
    item_vecs: list[list[float]],
    blend_weight: float = 0.2,
) -> list[float]:
    """
    Blend the query embedding with the centroid of viewed-item embeddings.

    final = L2_normalise((1 - w) * query_vec + w * mean(item_vecs))

    blend_weight=0.2 preserves 80% query intent while letting customer
    preference steer semantic nearest-neighbour retrieval.  Returns the
    original query_vec unchanged when item_vecs is empty.
    """
    if not item_vecs or not query_vec:
        return query_vec

    dim = len(query_vec)
    avg = [sum(v[i] for v in item_vecs) / len(item_vecs) for i in range(dim)]
    blended = [
        (1.0 - blend_weight) * q + blend_weight * p
        for q, p in zip(query_vec, avg)
    ]

    norm = math.sqrt(sum(x * x for x in blended))
    return [x / norm for x in blended] if norm > 0.0 else query_vec


async def fetch_preference_vector(
    ctx: SearchContext,
    collections: list[str],
    query_vec: list[float],
    blend_weight: float = 0.2,
) -> list[float]:
    """
    Retrieve embedding vectors for seed item ids from Typesense and blend
    them into ``query_vec`` via ``blend_preference_vector``.

    Scans each collection in order, stopping at the first match per seed id.
    Falls back to the original ``query_vec`` when no item vectors are found
    (e.g. items not yet indexed, or VOYAGE_API_KEY not set).
    """
    if not ctx.vector_seed_ids or not query_vec:
        return query_vec

    from isp.services.infra import typesense_client as ts

    item_vecs: list[list[float]] = []
    for item_id in ctx.vector_seed_ids:
        for col in collections:
            doc = ts.retrieve_document(col, item_id)
            if doc and isinstance(doc.get("vec"), list) and doc["vec"]:
                item_vecs.append(doc["vec"])
                break

    return blend_preference_vector(query_vec, item_vecs, blend_weight)
