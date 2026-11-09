# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for query_boost.clustering — pure, no DB or Typesense."""
from __future__ import annotations

import pytest

from isp.services.domain.query_boost import clustering as c


class TestNormalizeQuery:
    @pytest.mark.parametrize("raw, expected", [
        ("  Cheap   iPhone ", "cheap iphone"),
        ("IPHONE\t17", "iphone 17"),
        ("", ""),
        (None, ""),
    ])
    def test_normalizes(self, raw, expected):
        assert c.normalize_query(raw) == expected


class TestCosine:
    def test_identical_is_one(self):
        assert c.cosine([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)

    def test_orthogonal_is_zero(self):
        assert c.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    @pytest.mark.parametrize("a, b", [([1.0], [1.0, 0.0]), ([0.0, 0.0], [1.0, 0.0]), ([], [])])
    def test_degenerate_inputs_are_zero(self, a, b):
        assert c.cosine(a, b) == 0.0


class TestClusterQueries:
    def test_similar_queries_share_a_cluster_and_different_ones_do_not(self):
        counts = {"cheap iphone": 5, "budget iphone": 3, "galaxy fold": 4}
        vectors = {
            "cheap iphone":  [1.0, 0.0, 0.0],
            "budget iphone": [0.95, 0.1, 0.0],   # cos ≈ 0.99 with "cheap iphone"
            "galaxy fold":   [0.0, 1.0, 0.0],
        }
        clusters = c.cluster_queries(counts, vectors)
        by_query = {q: cl.cluster_id for cl in clusters for q in cl.queries}
        assert by_query["cheap iphone"] == by_query["budget iphone"]
        assert by_query["galaxy fold"] != by_query["cheap iphone"]
        assert len(clusters) == 2

    def test_most_frequent_query_anchors_cluster_zero(self):
        counts = {"a": 1, "b": 9}
        vectors = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
        clusters = c.cluster_queries(counts, vectors)
        assert clusters[0].queries == ["b"]

    def test_query_without_vector_gets_its_own_cluster(self):
        counts = {"x": 2, "y": 1}
        clusters = c.cluster_queries(counts, {"x": [1.0, 0.0]})
        assert [cl.queries for cl in clusters] == [["x"], ["y"]]
        assert clusters[1].centroid is None

    def test_threshold_controls_merging(self):
        counts = {"a": 2, "b": 1}
        vectors = {"a": [1.0, 0.0], "b": [0.8, 0.6]}  # cos = 0.8
        assert len(c.cluster_queries(counts, vectors, threshold=0.85)) == 2
        assert len(c.cluster_queries(counts, vectors, threshold=0.75)) == 1

    def test_centroid_is_unit_length(self):
        clusters = c.cluster_queries({"a": 2, "b": 1}, {"a": [3.0, 0.0], "b": [2.9, 0.3]})
        (cl,) = clusters
        assert sum(v * v for v in cl.centroid) == pytest.approx(1.0)

    def test_output_is_deterministic_on_ties(self):
        counts = {"b": 1, "a": 1}
        clusters = c.cluster_queries(counts, {})
        assert [cl.queries for cl in clusters] == [["a"], ["b"]]


class TestMatchCluster:
    Q2C = {"cheap iphone": 0, "galaxy fold": 1}
    CENTROIDS = {0: [1.0, 0.0], 1: [0.0, 1.0]}

    def test_exact_normalized_text_wins_without_a_vector(self):
        assert c.match_cluster("  Cheap IPHONE ", None, self.Q2C, self.CENTROIDS) == 0

    def test_unseen_query_matches_nearest_centroid(self):
        assert c.match_cluster("budget iphone", [0.97, 0.2], self.Q2C, self.CENTROIDS) == 0

    def test_unseen_query_below_threshold_matches_nothing(self):
        assert c.match_cluster("laptop", [0.7, 0.7], self.Q2C, self.CENTROIDS) is None

    def test_unseen_query_without_vector_matches_nothing(self):
        assert c.match_cluster("budget iphone", None, self.Q2C, self.CENTROIDS) is None
