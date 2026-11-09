# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Query-intent clustering for the learned query boost (Case B).

Literal query strings fragment click data too much: "cheap iphone",
"budget iphone" and "iphone under 500" should share evidence. Queries are
grouped by the cosine similarity of the embeddings the search path already
computes (``embedding_service.embed_query``), so no separate NLP pipeline is
needed.

Algorithm: greedy leader clustering. Queries are visited most-frequent
first; each joins the existing cluster whose centroid is most similar, if
that similarity is at least ``threshold``, otherwise it starts a new
cluster. Frequent queries therefore anchor clusters, and the result is
deterministic for a given input. It is O(queries × clusters), which is fine
for the few hundred distinct queries one training run keeps.

A query without an embedding (no embedding provider configured, or the call
failed) becomes its own single-query cluster. It can still be matched at
serve time by its exact normalised text.

Everything here is pure: no I/O, no globals.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Cosine similarity a query must reach to join a cluster, and that a live
# query must reach to be matched to one. 0.85 keeps paraphrases together
# ("cheap iphone" / "budget iphone") while separating different products.
DEFAULT_THRESHOLD = 0.85

_WS_RE = re.compile(r"\s+")


def normalize_query(q: str | None) -> str:
    """Lower-case, trim and collapse whitespace. Used for both training and lookup."""
    return _WS_RE.sub(" ", (q or "").strip().lower())


def _unit(vec: list[float]) -> list[float] | None:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return None
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0.0 for mismatched lengths or zero vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@dataclass
class QueryCluster:
    cluster_id: int
    queries: list[str] = field(default_factory=list)
    # Unit-length mean of the members' unit vectors; None for a cluster made
    # from a query that had no embedding.
    centroid: list[float] | None = None
    _sum: list[float] | None = field(default=None, repr=False)

    def add(self, query: str, unit_vec: list[float] | None) -> None:
        self.queries.append(query)
        if unit_vec is None:
            return
        if self._sum is None:
            self._sum = list(unit_vec)
        else:
            self._sum = [s + v for s, v in zip(self._sum, unit_vec)]
        self.centroid = _unit(self._sum)


def cluster_queries(
    query_counts: dict[str, int],
    vectors: dict[str, list[float]],
    threshold: float = DEFAULT_THRESHOLD,
) -> list[QueryCluster]:
    """
    Group normalised queries into intent clusters.

    ``query_counts`` maps each normalised query to how often it was seen and
    sets the visiting order (most frequent first, ties broken alphabetically
    so the output is stable). ``vectors`` maps a query to its embedding; a
    query missing from it, or with an empty/zero vector, gets its own
    cluster.
    """
    ordered = sorted(query_counts, key=lambda q: (-query_counts[q], q))
    clusters: list[QueryCluster] = []
    for q in ordered:
        unit_vec = _unit(vectors[q]) if vectors.get(q) else None
        best: QueryCluster | None = None
        best_sim = threshold
        if unit_vec is not None:
            for c in clusters:
                if c.centroid is None or len(c.centroid) != len(unit_vec):
                    continue
                sim = cosine(unit_vec, c.centroid)
                if sim >= best_sim:
                    best, best_sim = c, sim
        if best is None:
            best = QueryCluster(cluster_id=len(clusters))
            clusters.append(best)
        best.add(q, unit_vec)
    return clusters


def match_cluster(
    query: str,
    vec: list[float] | None,
    query_to_cluster: dict[str, int],
    centroids: dict[int, list[float]],
    threshold: float = DEFAULT_THRESHOLD,
) -> int | None:
    """
    Find the cluster a live query belongs to.

    An exact normalised-text match wins, and needs no embedding. Otherwise,
    when the caller has the query's vector, the nearest centroid is used if
    it clears ``threshold``. Returns None when nothing matches, in which case
    no learned boost applies.
    """
    norm = normalize_query(query)
    if norm in query_to_cluster:
        return query_to_cluster[norm]
    if not vec:
        return None
    best_id: int | None = None
    best_sim = threshold
    for cid, centroid in centroids.items():
        sim = cosine(vec, centroid)
        if sim >= best_sim:
            best_id, best_sim = cid, sim
    return best_id
