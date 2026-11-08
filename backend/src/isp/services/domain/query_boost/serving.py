# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Query-time side of the learned query boost (Case B).

Search routers call ``resolve()`` once per request to get ``{doc_id: boost}``
for the query, then fold ``make_hook()`` into their existing post-RRF hook
chain with ``chain_hooks()``. Nothing is computed per document beyond a dict
lookup and one addition.

Magnitude: why a small additive term, not the design doc's 0.3–0.5
---------------------------------------------------------------------
The design suggested calibrating Case B to the ``boost_queries_*`` range
(+0.3 to +0.5). On the RRF scale, where the #1 result scores about
1/61 ≈ 0.016, that would let one noisy click estimate beat every
relevance signal. That is the same failure as the 2026-09 "Galaxy fold for
'iphone 17'" popularity bug (see ``docs/scoreBoost.md``). Instead:

    score += boost · MAX_SCORE_BOOST          boost ∈ [0, 1]

``MAX_SCORE_BOOST = 0.008`` is about half of the top RRF score. A doc with
the strongest learned evidence can climb from roughly rank 30 to the top,
which is the point of learning from clicks. It still stays far below the
explicit merchandising levers (``is_featured`` +0.5, ``boost_queries_*``
+0.3/+0.5) and pinned hits, which keep their "operator wins" guarantee.

Never applies on empty-query browse: that is Case A's job.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from isp.services.domain.query_boost import clustering, store
from isp.services.domain.query_boost.store import QueryBoostModel

MAX_SCORE_BOOST = 0.008

Hook = Callable[[dict, dict], None]
Embedder = Callable[[str], Awaitable[list[float]]]


def _is_browse(q: str | None) -> bool:
    s = (q or "").strip()
    return not s or s == "*"


def boosts_for_query(
    model: QueryBoostModel | None,
    q: str,
    vec: list[float] | None = None,
    threshold: float = clustering.DEFAULT_THRESHOLD,
) -> dict[str, float]:
    """Pure lookup: the boosts of the cluster ``q`` belongs to, or {}."""
    if model is None or model.is_empty or _is_browse(q):
        return {}
    cid = clustering.match_cluster(q, vec, model.query_to_cluster, model.centroids, threshold)
    if cid is None:
        return {}
    return model.boosts.get(cid, {})


async def resolve(
    index_id: str,
    q: str,
    *,
    embed_model: str,
    embed: Embedder | None = None,
) -> dict[str, float]:
    """
    Boosts for ``q`` on one index.

    Tries an exact query match first, with no embedding. If that misses and
    the caller supplies ``embed``, it embeds the query and matches by
    centroid. Search routers pass ``embed`` only in hybrid/semantic mode,
    where they embed the query anyway, so it is a cache hit there, and
    lexical mode never pays for an embedding. Centroid matching is skipped
    when the index's embedding model has changed since training, because
    vectors from different models can't be compared.
    """
    if _is_browse(q):
        return {}
    model = await store.load_model(index_id)
    if model is None or model.is_empty:
        return {}
    if clustering.normalize_query(q) in model.query_to_cluster:
        # Known query: its own cluster decides, even when that cluster learned
        # no boost. Never re-match it by vector into a neighbouring cluster.
        return boosts_for_query(model, q)
    if embed is None or not model.centroids or model.embed_model != embed_model:
        return {}
    try:
        vec = await embed(q)
    except Exception:
        return {}
    return boosts_for_query(model, q, vec)


def has_exact_title_match(all_docs: dict, q: str) -> bool:
    """
    True when some candidate's title, or one of its aliases, equals the query
    after normalisation (case and whitespace).

    ``aliases`` may be a string or a list of strings; both are checked.
    """
    norm = clustering.normalize_query(q)
    if not norm:
        return False
    for doc in all_docs.values():
        if clustering.normalize_query(doc.get("title")) == norm:
            return True
        aliases = doc.get("aliases")
        if isinstance(aliases, str):
            aliases = [aliases]
        if isinstance(aliases, list) and any(
            isinstance(a, str) and clustering.normalize_query(a) == norm for a in aliases
        ):
            return True
    return False


def make_hook(
    boosts: dict[str, float],
    *,
    q: str,
    key: Callable[[str, dict], str] | None = None,
) -> Hook | None:
    """
    Post-RRF hook that adds ``boost · MAX_SCORE_BOOST`` to each boosted doc.

    ``key(doc_id, doc)`` maps a candidate to its key in ``boosts``. The
    default is the doc id. Catalog search, which merges several indexes,
    passes a key that includes the source index. Returns None when there is
    nothing to boost, so callers can skip chaining.

    **Exact-title protection.** If any candidate's title or alias exactly
    equals ``q``, the hook changes nothing. Even a small learned boost
    (0.1 × 0.008 = 0.0008) is larger than the RRF gap between neighbouring
    ranks, so it could lift a sibling variant ("iPhone 17 Pro Max") over
    the exact match ("iPhone 17"). An exact match is a deterministic
    answer, and the design keeps deterministic matches ahead of the learned
    layer. The check runs on the candidate set inside the hook, because
    that is the only place the matched documents are known.
    """
    if not boosts:
        return None

    def hook(scores: dict, all_docs: dict) -> None:
        if has_exact_title_match(all_docs, q):
            return
        for did, doc in all_docs.items():
            b = boosts.get(key(did, doc) if key else did)
            if b:
                scores[did] = scores.get(did, 0.0) + b * MAX_SCORE_BOOST

    return hook


def chain_hooks(*hooks: Hook | None) -> Hook | None:
    """Run the non-None hooks in order. Returns None if all are None, or the hook itself if only one isn't."""
    active = [h for h in hooks if h is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def chained(scores: dict, all_docs: dict) -> None:
        for h in active:
            h(scores, all_docs)

    return chained
