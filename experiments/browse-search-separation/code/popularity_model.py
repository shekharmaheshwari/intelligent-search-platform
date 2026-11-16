"""Pure popularity features and estimator shared by production and offline experiments.

No database, network, application settings, or catalog writes are imported here.
"""
from __future__ import annotations
import json
import math
import time

def position_weight(rank: int) -> float:
    """Same position-decay curve as analytics_service.aggregate_popularity, redefined
    locally (not imported) to keep this module fully decoupled — trivial duplication,
    not worth an inter-module dependency for one formula."""
    return 1.0 / math.log2(rank + 2)


def parse_meta(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def doc_features(doc: dict, affinity_dims: list[dict]) -> dict[str, float | str]:
    """
    Build a flat feature dict for one document: one-hot-able categorical
    values for each enabled affinity dimension, is_featured, rating signal
    (when present), and a bounded recency feature. Missing fields are simply
    omitted — no per-vertical branching, works the same for a phone or a
    houseplant.
    """
    meta = parse_meta(doc.get("metadata"))
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


def make_estimator():
    """Preserve the production estimator configuration in one place."""
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(max_iter=1000)


def fit_scores(documents: dict[str, dict], rows: list[dict], affinity_dims: list[dict]):
    """Fit offline and return probabilities without normalizing or writing scores.

    Rows must be selected by the caller; no traffic policy is implicit here.
    None denotes insufficient usable data, classes, or features.
    """
    from sklearn.feature_extraction import DictVectorizer
    features = {key: doc_features(doc, affinity_dims) for key, doc in documents.items()}
    usable = [row for row in rows if row['doc_id'] in features]
    if len(usable) < 5 or len({bool(r['clicked']) for r in usable}) < 2:
        return None
    vectorizer = DictVectorizer(sparse=True)
    X = vectorizer.fit_transform([features[r['doc_id']] for r in usable])
    if X.shape[1] == 0:
        return None
    model = make_estimator()
    model.fit(X, [int(r['clicked']) for r in usable],
              sample_weight=[position_weight(r['rank']) for r in usable])
    ids = list(features)
    scores = model.predict_proba(vectorizer.transform([features[k] for k in ids]))[:, 1]
    return dict(zip(ids, map(float, scores)))
