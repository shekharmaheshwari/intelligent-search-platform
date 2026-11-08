# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for query_boost.scoring — pure, no DB or Typesense."""
from __future__ import annotations

import pytest

from isp.services.domain.query_boost import scoring as s
from isp.services.domain.query_boost.scoring import Event


def _impressions(cluster: int, doc: str, rank: int, n: int, clicks: int = 0) -> list[Event]:
    """``n`` impression rows plus ``clicks`` separate click rows, as analytics logs them."""
    return ([Event(cluster, doc, rank, False) for _ in range(n)]
            + [Event(cluster, doc, rank, True) for _ in range(clicks)])


class TestPositionWeight:
    def test_rank_zero_is_one(self):
        assert s.position_weight(0) == pytest.approx(1.0)

    def test_decays_with_rank(self):
        assert s.position_weight(0) > s.position_weight(1) > s.position_weight(10)

    def test_negative_rank_is_clamped(self):
        assert s.position_weight(-3) == pytest.approx(1.0)


class TestAggregate:
    def test_click_rows_are_not_counted_as_impressions(self):
        # Regression: analytics writes impressions and clicks as separate rows.
        # 10 impression rows + 10 click rows is a 100% CTR, not 50%.
        stats = s.aggregate(_impressions(0, "a", 0, 10, clicks=10))[0]["a"]
        assert (stats.n_impressions, stats.n_clicks) == (10, 10)
        assert stats.clicks_w / stats.impressions_w == pytest.approx(1.0)

    def test_sums_weighted_and_raw_counts_per_cluster_and_doc(self):
        stats = s.aggregate([Event(0, "a", 0, False), Event(0, "a", 0, False),
                             Event(0, "a", 0, True), Event(1, "a", 0, True)])
        assert stats[0]["a"].n_impressions == 2
        assert stats[0]["a"].n_clicks == 1
        assert stats[0]["a"].impressions_w == pytest.approx(2.0)
        assert stats[0]["a"].clicks_w == pytest.approx(1.0)
        assert stats[1]["a"].n_clicks == 1

    def test_click_without_impression_row_implies_one(self):
        # A missed impression beacon must not produce a CTR above 1.
        stats = s.aggregate([Event(0, "a", 3, True)])[0]["a"]
        assert stats.n_impressions == 1
        assert stats.impressions_w == pytest.approx(stats.clicks_w)


class TestBoostFromCtr:
    def test_at_baseline_is_zero(self):
        assert s.boost_from_ctr(0.2, 0.2) == 0.0

    def test_below_baseline_is_clamped_to_zero(self):
        assert s.boost_from_ctr(0.1, 0.2) == 0.0

    def test_perfect_ctr_is_one(self):
        assert s.boost_from_ctr(1.0, 0.2) == pytest.approx(1.0)

    def test_baseline_of_one_gives_zero(self):
        assert s.boost_from_ctr(1.0, 1.0) == 0.0


class TestComputeBoosts:
    def test_frequently_clicked_doc_gets_the_largest_boost(self):
        events = (_impressions(0, "good", 0, 20, clicks=15)
                  + _impressions(0, "meh", 1, 20, clicks=2)
                  + _impressions(0, "never", 2, 20, clicks=0))
        boosts = {b.doc_id: b.boost for b in s.compute_boosts(events)}
        assert boosts["good"] > boosts.get("meh", 0.0)
        assert "never" not in boosts  # promotions only — no negative boost

    def test_sparse_evidence_is_shrunk_toward_zero(self):
        # One click on one impression vs. 15/20 — same raw CTR order, but the
        # single-impression doc must not get a comparable boost.
        events = (_impressions(0, "steady", 0, 20, clicks=15)
                  + _impressions(0, "lucky", 0, 1, clicks=1)
                  + _impressions(0, "filler", 0, 40, clicks=0))
        boosts = {b.doc_id: b.boost for b in s.compute_boosts(events)}
        assert boosts["steady"] > boosts["lucky"]

    def test_more_prior_strength_means_smaller_boosts(self):
        events = _impressions(0, "a", 0, 10, clicks=8) + _impressions(0, "b", 0, 10, clicks=0)
        weak = {b.doc_id: b.boost for b in s.compute_boosts(events, prior_strength=1)}
        strong = {b.doc_id: b.boost for b in s.compute_boosts(events, prior_strength=50)}
        assert weak["a"] > strong["a"]

    def test_evidence_from_low_ranks_is_trusted_less(self):
        # Same raw CTR (5/10), but "deep" was shown at rank 9, where each
        # event carries weight ≈ 0.29 instead of 1.0 — less sure the user even
        # looked — so its CTR is shrunk harder toward the baseline.
        events = (_impressions(0, "top", 0, 10, clicks=5)
                  + _impressions(0, "deep", 9, 10, clicks=5)
                  + _impressions(0, "filler", 3, 30, clicks=0))
        boosts = {b.doc_id: b.boost for b in s.compute_boosts(events)}
        assert boosts["top"] > boosts["deep"] > 0

    def test_cluster_without_clicks_learns_nothing(self):
        assert s.compute_boosts(_impressions(0, "a", 0, 10)) == []

    def test_clusters_are_scored_independently(self):
        events = (_impressions(0, "a", 0, 10, clicks=8) + _impressions(0, "b", 0, 10)
                  + _impressions(1, "b", 0, 10, clicks=8) + _impressions(1, "a", 0, 10))
        by_key = {(b.cluster_id, b.doc_id) for b in s.compute_boosts(events)}
        assert (0, "a") in by_key and (1, "b") in by_key
        assert (0, "b") not in by_key and (1, "a") not in by_key

    def test_boosts_are_within_unit_range_and_sorted(self):
        events = _impressions(0, "a", 0, 50, clicks=50) + _impressions(0, "b", 0, 50, clicks=5)
        boosts = s.compute_boosts(events)
        assert all(0.0 <= b.boost <= 1.0 for b in boosts)
        assert [b.boost for b in boosts] == sorted((b.boost for b in boosts), reverse=True)

    def test_min_boost_filters_small_values(self):
        events = _impressions(0, "a", 0, 10, clicks=3) + _impressions(0, "b", 0, 10, clicks=2)
        assert s.compute_boosts(events, min_boost=0.99) == []
