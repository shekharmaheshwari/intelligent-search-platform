"""
Shared relevance floors for Typesense hit lists.

Both the chat pipeline (``chat/service.py``) and the plain catalog search
used by the listing page (``typesense_client.search_with_vec``) run the same
shape of RRF merge: a lexical/structured pass plus a semantic (vector) pass,
combined by rank. Neither Typesense leg has a built-in "is this actually
relevant" floor — a vector search always returns its *k* nearest neighbours
even when nothing in the catalog is related to the query, and Typesense's
typo-tolerant lexical matching can return a "hit" on a single incidental
word overlap (e.g. the query token "17" matching a product's price text
"$17.99"). Without a floor, a query with zero genuine matches still fills
the result list with "the least-bad thing available."

This module was extracted out of chat/service.py once a second consumer
(the listing page's search) needed the identical logic — see
docs/multiIntentConjunctionSearchDesign.md for the chat-side history that
led to these floors, though this module itself is chat-agnostic: it takes
plain Typesense hit dicts and a query string, nothing more.
"""

from __future__ import annotations

# Calibrated empirically against voyage-3, across two different catalogs —
# the "least-bad" distance for a genuine mismatch is domain-dependent (short,
# alphanumeric-heavy titles read as closer to a garbled query than long plant
# descriptions do), so this floor is set from the worst-case gap observed,
# not either domain alone:
#   plants:      real match ~0.47-0.50   garbage ~0.87-0.88
#   accessories: real match ~0.47-0.50   garbage ~0.68-0.74
# 0.6 sits with margin above every real match seen and below every garbage
# score seen. `vector_distance` is cosine distance (0 = identical, 2 =
# opposite); Typesense returns it per-hit whenever a vector_query ran.
#
# The app lets an operator switch the embedding model per-index or globally
# (see settings_service.AVAILABLE_MODELS) — a different model's cosine-
# distance distribution won't line up with 0.6, so this floor is only valid
# for the model it was measured against. Keyed by model id rather than a bare
# constant so switching models can't silently apply a wrong-domain floor
# everywhere; add an entry here only after repeating the calibration exercise
# above against that model. Re-check against any new document domain too
# before trusting an existing entry there — none of this is derived from
# first principles, only from what's been measured so far.
SEM_MAX_DISTANCE_BY_MODEL: dict[str, float] = {
    "voyage-3": 0.6,
}


def sem_max_distance_for_model(embed_model: str | None) -> float | None:
    """
    Return the calibrated cosine-distance floor for ``embed_model``, or None
    if it has never been calibrated (including when the caller doesn't know
    which model produced these vectors at all) — callers must treat None as
    "don't filter," not guess at the nearest known value.
    """
    if not embed_model:
        return None
    return SEM_MAX_DISTANCE_BY_MODEL.get(embed_model)

# A genuine match on a short query needs most of its tokens to actually be
# present; Typesense's `text_match_info.tokens_matched` gives an exact count.
LEX_MIN_TOKEN_COVERAGE = 0.5


def passes_token_coverage(hit: dict, query_token_count: int) -> bool:
    """
    True if this lexical/structured hit's matched-token count clears the
    coverage floor for a query of this length.

    A 2-token query (e.g. "iphone 17") sits right on the general 50% floor
    from a single incidental overlap — observed case: "17" alone matched a
    plant's price text ("$17.99"), which is no evidence of a real match at
    all. Two tokens is too few for a fractional floor to mean anything, so
    a 2-token query requires both to match.
    """
    if query_token_count <= 1:
        return True  # a single-token query already requires that token to match
    matched = hit.get("text_match_info", {}).get("tokens_matched")
    if matched is None:
        return True  # fail open — no info to judge by, don't drop a hit over it
    required = 1.0 if query_token_count == 2 else LEX_MIN_TOKEN_COVERAGE
    return (matched / query_token_count) >= required


def filter_lexical_hits(hits: list[dict], query: str) -> list[dict]:
    """Drop lexical/structured hits that don't clear the token-coverage floor."""
    query_token_count = len(query.split())
    return [h for h in hits if passes_token_coverage(h, query_token_count)]


def filter_semantic_hits(hits: list[dict], embed_model: str | None = None) -> list[dict]:
    """
    Drop semantic/vector hits beyond the cosine-distance floor.

    ``embed_model`` should be the model that actually produced these hits'
    vectors (e.g. ``meta.embed_model or embedding_service.get_current_model()``).
    Uncalibrated (including unknown/omitted) is a no-op — a wrong floor is
    worse than none, since a real match would be dropped with no visible error.
    """
    max_distance = sem_max_distance_for_model(embed_model)
    if max_distance is None:
        return hits
    return [h for h in hits if h.get("vector_distance", 0) <= max_distance]
