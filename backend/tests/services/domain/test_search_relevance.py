"""Pure-function tests for search_relevance.py's relevance floors."""

from isp.services.domain import search_relevance as sr


def _hit(tokens_matched: int | None = None, vector_distance: float | None = None) -> dict:
    hit: dict = {}
    if tokens_matched is not None:
        hit["text_match_info"] = {"tokens_matched": tokens_matched}
    if vector_distance is not None:
        hit["vector_distance"] = vector_distance
    return hit


# ── passes_token_coverage ────────────────────────────────────────────────────

def test_single_token_query_always_passes():
    assert sr.passes_token_coverage(_hit(tokens_matched=0), 1) is True


def test_missing_text_match_info_fails_open():
    assert sr.passes_token_coverage({}, 3) is True


def test_two_token_query_requires_full_coverage():
    # "iphone 17" matching only "17" (e.g. a price "$17.99") must be dropped.
    assert sr.passes_token_coverage(_hit(tokens_matched=1), 2) is False
    assert sr.passes_token_coverage(_hit(tokens_matched=2), 2) is True


def test_three_token_query_uses_half_floor():
    # "iphone screen protector" matching only "protect" (1 of 3) must be dropped.
    assert sr.passes_token_coverage(_hit(tokens_matched=1), 3) is False
    # 2 of 3 clears the 50% floor.
    assert sr.passes_token_coverage(_hit(tokens_matched=2), 3) is True
    assert sr.passes_token_coverage(_hit(tokens_matched=3), 3) is True


# ── filter_lexical_hits ──────────────────────────────────────────────────────

def test_filter_lexical_hits_drops_weak_matches():
    hits = [_hit(tokens_matched=3), _hit(tokens_matched=1)]
    out = sr.filter_lexical_hits(hits, "iphone screen protector")
    assert out == [hits[0]]


def test_filter_lexical_hits_keeps_all_for_garbage_query_with_no_hits():
    assert sr.filter_lexical_hits([], "324sdfasdasdasdasd") == []


# ── filter_semantic_hits ─────────────────────────────────────────────────────

def test_filter_semantic_hits_drops_far_neighbors():
    close = _hit(vector_distance=0.48)
    far = _hit(vector_distance=0.88)
    assert sr.filter_semantic_hits([close, far], "voyage-3") == [close]


def test_filter_semantic_hits_keeps_boundary_value():
    boundary = _hit(vector_distance=sr.SEM_MAX_DISTANCE_BY_MODEL["voyage-3"])
    assert sr.filter_semantic_hits([boundary], "voyage-3") == [boundary]


def test_filter_semantic_hits_missing_distance_defaults_to_kept():
    # No vector_distance field at all (e.g. a malformed hit) — treated as 0,
    # i.e. kept, rather than dropped over missing data.
    assert sr.filter_semantic_hits([{}], "voyage-3") == [{}]


def test_filter_semantic_hits_noop_for_uncalibrated_model():
    # No floor has ever been measured for this model (or the caller doesn't
    # know which model produced these vectors at all) — must not filter
    # anything, since a wrong floor silently drops real matches.
    far = _hit(vector_distance=1.9)
    assert sr.filter_semantic_hits([far], "some-other-model") == [far]
    assert sr.filter_semantic_hits([far]) == [far]
    assert sr.filter_semantic_hits([far], None) == [far]
