"""
Customer action history → personalised search context.

Two main outputs:

1. ``SearchContext`` — affinity maps derived from action weights.
   Applied as additive score boosts after RRF merge so they nudge rather
   than dominate relevance.

2. Preference vector blending — embedding vectors of recently viewed items
   are fetched from Typesense and blended into the query vector before the
   semantic search pass, steering ANN retrieval toward products the customer
   actually engages with.

None of this is stateful: callers pass in the action list on every request.
"""
from __future__ import annotations
import json
import math
from dataclasses import dataclass, field

_ACTION_WEIGHT: dict[str, float] = {
    "purchase": 3.0,
    "cart":     2.0,
    "view":     1.0,
    "search":   0.5,
    "category": 0.3,
}

_VECTOR_ELIGIBLE = {"view", "cart", "purchase"}


@dataclass
class SearchContext:
    """Pre-computed affinity context derived from a customer's action history."""
    category_affinity:     dict[str, float] = field(default_factory=dict)
    manufacturer_affinity: dict[str, float] = field(default_factory=dict)
    type_affinity:         dict[str, float] = field(default_factory=dict)
    viewed_ids:            set[str]         = field(default_factory=set)
    # Ordered list of item ids whose vectors will seed preference blending.
    # Capped at 5 — retrieved synchronously from Typesense, so latency matters.
    vector_seed_ids:       list[str]        = field(default_factory=list)


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
            return {}
    return {}


def build_context(actions: list[dict], max_actions: int = 50) -> SearchContext:
    """
    Convert a flat list of customer action dicts into a ``SearchContext``.

    Each action is weighted by type (purchase > cart > view > search > category)
    and an optional caller-supplied ``weight`` multiplier.  All affinity scores
    are normalised to [0, 1] so boost magnitudes remain predictable regardless
    of how many actions are passed.
    """
    ctx = SearchContext()
    cat_raw:  dict[str, float] = {}
    mfr_raw:  dict[str, float] = {}
    type_raw: dict[str, float] = {}

    for action in actions[:max_actions]:
        action_type = action.get("type", "view")
        base_w      = _ACTION_WEIGHT.get(action_type, 1.0)
        caller_w    = float(action.get("weight", 1.0))
        w           = base_w * max(caller_w, 0.0)

        if action.get("category"):
            k = action["category"].lower().strip()
            if k:
                cat_raw[k] = cat_raw.get(k, 0.0) + w

        if action.get("manufacturer"):
            k = action["manufacturer"].lower().strip()
            if k:
                mfr_raw[k] = mfr_raw.get(k, 0.0) + w

        if action.get("item_type"):
            k = action["item_type"].lower().strip()
            if k:
                type_raw[k] = type_raw.get(k, 0.0) + w

        if action.get("item_id") and action_type in _VECTOR_ELIGIBLE:
            ctx.viewed_ids.add(action["item_id"])
            if len(ctx.vector_seed_ids) < 5:
                ctx.vector_seed_ids.append(action["item_id"])

    def _norm(raw: dict[str, float]) -> dict[str, float]:
        if not raw:
            return {}
        mx = max(raw.values())
        return {k: round(v / mx, 4) for k, v in raw.items()} if mx > 0 else {}

    ctx.category_affinity     = _norm(cat_raw)
    ctx.manufacturer_affinity = _norm(mfr_raw)
    ctx.type_affinity         = _norm(type_raw)
    return ctx


def context_boost(
    doc: dict,
    ctx: SearchContext,
    *,
    apply_affinity: bool = True,
    apply_viewed_penalty: bool = True,
) -> float:
    """
    Compute the additive context score for a single document.

    Boost magnitudes are kept small relative to editorial boosts
    (is_featured +0.5, popularity ≤ +0.5) so context nudges rather than
    overrides relevance.

      +0.20 × category affinity       (when apply_affinity=True)
      +0.15 × manufacturer affinity   (when apply_affinity=True)
      +0.10 × source_type affinity    (when apply_affinity=True)
      -0.05   demotion for viewed      (when apply_viewed_penalty=True)
    """
    meta  = parse_meta(doc.get("metadata"))
    boost = 0.0

    if apply_affinity:
        doc_type = (doc.get("source_type") or meta.get("type") or "").lower()
        if doc_type and doc_type in ctx.type_affinity:
            boost += 0.10 * ctx.type_affinity[doc_type]

        doc_cat = (meta.get("category") or "").lower()
        if doc_cat and doc_cat in ctx.category_affinity:
            boost += 0.20 * ctx.category_affinity[doc_cat]

        doc_mfr = (meta.get("manufacturer") or "").lower()
        if doc_mfr and doc_mfr in ctx.manufacturer_affinity:
            boost += 0.15 * ctx.manufacturer_affinity[doc_mfr]

    if apply_viewed_penalty and doc.get("id") in ctx.viewed_ids:
        boost -= 0.05

    return boost


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

    This is the primary personalisation mechanism: the ANN (approximate nearest
    neighbour) pass in Typesense uses the blended vector, so semantically similar
    items to what the customer has viewed naturally rank higher.
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
                break  # found in this collection, skip others

    return blend_preference_vector(query_vec, item_vecs, blend_weight)
