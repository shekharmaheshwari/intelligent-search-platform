"""
Trained browse-popularity re-scorer (Case A of the trained re-scorer layer —
see ``docs/trainedRescorerDesign.md``).

Deliberately decoupled from ``analytics_service.py``: this module does not
import it, is not imported by it, and can be deleted without touching the
existing formula-based ``aggregate_popularity``/``apply_popularity`` path.
It reads the same ``search_events`` table and writes the same ``popularity``
field via the same ``update_document_fields`` call, but through its own
code path.

v1 scope, deliberately:
  - Trained **per index**, not pooled across indexes (the design doc's
    longer-term recommendation) — keeps this consistent with the existing
    per-index ``?index_id=`` endpoint shape and avoids a cross-index join in
    the first pass. Revisit if a specific index's traffic proves too sparse.
  - Position-debiasing uses the same ``1/log2(rank+2)`` decay already used
    in ``analytics_service.aggregate_popularity`` — but as a training
    ``sample_weight``, not a hand-summed formula. Proper IPS/propensity
    debiasing is a documented v2 follow-up, not built here.
  - Training data is narrowed to **no-query (browse) impressions**
    (``query = ''``) — a deliberate difference from today's formula, which
    pools all traffic regardless of query.
  - Offline eval is a simple held-out AUC, not the full counterfactual
    (IPS/nDCG) harness described as a v2 follow-up in the design doc.

scikit-learn / numpy are imported lazily inside functions, not at module
top level, matching this project's existing lazy-import convention for
optional heavy dependencies (see the chat-provider SDK comment in
``pyproject.toml``).
"""
from __future__ import annotations
import json
import math
import time

from isp.services.infra.database import get_pool
from isp.services.infra import typesense_client as ts
from isp.services.domain import index_service

_MIN_EVENTS = 5  # below this, training data is too thin to fit anything meaningful


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


async def _fetch_all_documents(index_id: str) -> dict[str, dict]:
    """
    Page through every document in an index via the same list_documents call
    that backs GET /v1/indexes/{id}/documents.

    Returns {} (rather than raising) if the collection doesn't exist or
    Typesense is unreachable — same defensive pattern as
    affinity_service.discover()'s sample fetch, so a training call degrades
    to "nothing to score" instead of a 500.
    """
    docs: dict[str, dict] = {}
    page = 1
    per_page = 250
    while True:
        try:
            raw = ts.list_documents(index_id, page, per_page, exclude_vec=True)
        except Exception:
            return {}
        hits = raw.get("hits", [])
        if not hits:
            break
        for hit in hits:
            doc = hit.get("document", {})
            doc_id = doc.get("id")
            if doc_id:
                docs[doc_id] = doc
        if len(hits) < per_page:
            break
        page += 1
    return docs


async def train_and_apply(index_id: str, days: int = 30) -> dict:
    """
    Train a browse-popularity model for one index and write scores back to
    Typesense. Returns a summary dict; also logs one row to training_runs.

    Mirrors aggregate_popularity's graceful-empty behavior: returns
    docs_scored=0 (no error) when there isn't enough browse-event data to
    train on, rather than failing.
    """
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupShuffleSplit
    from sklearn.metrics import roc_auc_score

    meta = await index_service.get_index(index_id)
    if not meta:
        raise ValueError(f"index not found: {index_id}")

    cutoff_ms = int((time.time() - days * 86400) * 1000)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT doc_id, rank, clicked
            FROM search_events
            WHERE index_id = $1
              AND query = ''
              AND created_at >= $2
            """,
            index_id, cutoff_ms,
        )

    result = {"docs_scored": 0, "n_events": len(rows), "auc": None}
    if len(rows) < _MIN_EVENTS:
        await _log_run(index_id, "logistic_regression", len(rows), 0, None)
        return result

    docs = await _fetch_all_documents(index_id)
    if not docs:
        await _log_run(index_id, "logistic_regression", len(rows), 0, None)
        return result

    doc_features = {doc_id: _doc_features(doc, meta.affinity_dimensions) for doc_id, doc in docs.items()}

    X_dicts: list[dict] = []
    y: list[int] = []
    w: list[float] = []
    example_doc_ids: list[str] = []
    for row in rows:
        feats = doc_features.get(row["doc_id"])
        if feats is None:
            continue  # doc since deleted from Typesense — same defensive skip as apply_popularity
        X_dicts.append(feats)
        y.append(1 if row["clicked"] else 0)
        w.append(_position_weight(row["rank"]))
        example_doc_ids.append(row["doc_id"])

    if len(y) < _MIN_EVENTS:
        # The raw query cleared _MIN_EVENTS, but enough of those rows pointed
        # at documents no longer in Typesense that the *usable* training set
        # (post-join) fell back below threshold. Recheck here rather than
        # trusting the raw row count from the query above.
        await _log_run(index_id, "logistic_regression", len(rows), 0, None)
        return result

    if len(set(y)) < 2:
        # Degenerate training set (all clicks or all impressions-only) —
        # nothing for a classifier to learn. Skip rather than error.
        await _log_run(index_id, "logistic_regression", len(rows), 0, None)
        return result

    vectorizer = DictVectorizer(sparse=True)
    X = vectorizer.fit_transform(X_dicts)

    if X.shape[1] == 0:
        # No affinity dimensions discovered yet and none of is_featured /
        # rating / indexed_at present on any document — every feature dict
        # came out empty. Nothing for the model to fit on.
        await _log_run(index_id, "logistic_regression", len(rows), 0, None)
        return result

    auc = None
    if len(y) >= 10:
        try:
            # Grouped by doc_id, not a plain random split: multiple events
            # for the same document share an identical feature vector, so a
            # per-event split can leak the same doc's features into both
            # train and test and inflate this AUC. Grouping keeps each
            # document entirely on one side.
            splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=0)
            train_idx, test_idx = next(splitter.split(X, y, groups=example_doc_ids))
            X_train, X_test = X[train_idx], X[test_idx]
            y_train = [y[i] for i in train_idx]
            y_test = [y[i] for i in test_idx]
            w_train = [w[i] for i in train_idx]
            eval_model = LogisticRegression(max_iter=1000)
            eval_model.fit(X_train, y_train, sample_weight=w_train)
            if len(set(y_test)) == 2:
                auc = float(roc_auc_score(y_test, eval_model.predict_proba(X_test)[:, 1]))
        except ValueError:
            auc = None  # e.g. a single class landed on one side of the group split — eval is best-effort

    model = LogisticRegression(max_iter=1000)
    model.fit(X, y, sample_weight=w)

    doc_ids = list(doc_features.keys())
    X_score = vectorizer.transform([doc_features[d] for d in doc_ids])
    raw_scores = model.predict_proba(X_score)[:, 1]

    max_raw = max(raw_scores) if len(raw_scores) else 0.0
    updated = 0
    if max_raw > 0:
        for doc_id, raw in zip(doc_ids, raw_scores):
            if raw <= 0:
                continue
            popularity = round(float(raw) / float(max_raw) * 1_000_000)
            if ts.update_document_fields(meta.collection_name, doc_id, {"popularity": popularity}):
                updated += 1

    await _log_run(index_id, "logistic_regression", len(rows), updated, auc)
    return {"docs_scored": updated, "n_events": len(rows), "auc": auc}


async def _log_run(index_id: str, model_type: str, n_events: int, n_docs_scored: int, auc: float | None) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO training_runs
                (index_id, case_name, model_type, n_events, n_docs_scored, metric_name, metric_value, trained_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            index_id, "A", model_type, n_events, n_docs_scored,
            "auc" if auc is not None else None, auc,
            int(time.time() * 1000),
        )
