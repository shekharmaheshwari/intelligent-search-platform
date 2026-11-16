"""Frozen pre-refactor feature logic for parity tests only."""
import json
import math
import time
def _position_weight(rank: int) -> float:
    """Same position-decay curve as analytics_service.aggregate_popularity, redefined
    locally (not imported) to keep this module fully decoupled — trivial duplication,
    not worth an inter-module dependency for one formula."""
    return 1.0 / math.log2(rank + 2)


def _parse_meta(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _doc_features(doc: dict, affinity_dims: list[dict]) -> dict[str, float | str]:
    """
    Build a flat feature dict for one document: one-hot-able categorical
    values for each enabled affinity dimension, is_featured, rating signal
    (when present), and a bounded recency feature. Missing fields are simply
    omitted — no per-vertical branching, works the same for a phone or a
    houseplant.
    """
    meta = _parse_meta(doc.get("metadata"))
    features: dict[str, float | str] = {}

    for dim in affinity_dims:
        if not dim.get("enabled", True):
            continue
        field = dim["field"]
        derived = dim.get("derived_from")
        if derived:
            # Derived dimensions (price_tier, {field}_tier) are buckets
            # computed on the fly from a raw numeric field — use that raw
            # numeric value directly as a continuous feature instead of
            # re-deriving the bucket. Strictly more informative for the
            # model, and avoids duplicating context_service.py's threshold
            # logic here.
            raw_val = meta.get(derived)
            if raw_val is not None:
                try:
                    features[f"num__{derived}"] = float(raw_val)
                except (TypeError, ValueError):
                    pass
            continue
        raw_val = doc.get(field) or meta.get(field)
        if isinstance(raw_val, str) and raw_val.strip():
            features[f"cat__{field}"] = raw_val.lower().strip()
        elif isinstance(raw_val, list) and raw_val:
            features[f"cat__{field}"] = str(raw_val[0]).lower().strip()

    if doc.get("is_featured"):
        features["is_featured"] = 1.0

    rating = doc.get("averageStarRating") or meta.get("averageStarRating")
    count = doc.get("ratingsCount") or meta.get("ratingsCount")
    if rating is not None and count is not None:
        try:
            features["num__rating_weighted"] = float(rating) * math.log1p(float(count))
        except (TypeError, ValueError):
            pass

    indexed_at = doc.get("indexed_at")
    if isinstance(indexed_at, (int, float)):
        days_since = max((time.time() - indexed_at) / 86400.0, 0.0)
        features["num__recency"] = 1.0 / (1.0 + days_since)

    return features


