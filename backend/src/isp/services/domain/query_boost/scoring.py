# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Turn click events into a learned boost per (query cluster, document).

For each cluster:

  1. Position weighting. Every event (impression and click alike) counts
     ``w = 1/log2(rank+2)``, the same decay Case A uses as its
     ``sample_weight``. Lower-ranked results were less likely to have been
     looked at, so their outcomes are trusted less and shrunk harder in
     step 3. This is the decay-first step. Proper IPS debiasing, which
     would instead up-weight a click at a low rank, is the documented
     follow-up, as for Case A.
  2. Baseline. ``p_c`` = the cluster's overall weighted CTR.
  3. Shrinkage (empirical Bayes). A document's CTR is pulled toward ``p_c``
     by ``prior_strength`` pseudo-impressions:
         shrunk = (clicks_w + m · p_c) / (impressions_w + m)
     With little data, shrunk ≈ p_c, so the boost is ≈ 0 and ranking falls
     back to everything else, including Case A's trained popularity. That is
     the design doc's "blend toward Case A when sparse", with no hard cutoff.
  4. Boost. Only lift above the baseline counts, scaled by the headroom
     left above it:
         boost = max(0, shrunk − p_c) / (1 − p_c)          ∈ [0, 1]
     v1 learns promotions only. A document that is shown and never clicked
     gets 0, not a penalty; demotion is a follow-up (see the doc).

Pure functions only: no I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Pseudo-impressions of the cluster baseline mixed into every document's CTR.
# 5 means a doc needs roughly 5 weighted impressions of its own before its
# own behaviour outweighs the baseline.
DEFAULT_PRIOR_STRENGTH = 5.0
# Boosts below this are dropped so the stored table stays small; they would
# move a score by less than 1% of the serve-time cap anyway.
MIN_STORED_BOOST = 0.01


def position_weight(rank: int) -> float:
    """1/log2(rank+2). Redefined here rather than imported, to keep Case B decoupled."""
    return 1.0 / math.log2(max(int(rank), 0) + 2)


@dataclass(frozen=True)
class Event:
    """One ``search_events`` row: an impression (clicked=False) or a click (clicked=True)."""
    cluster_id: int
    doc_id: str
    rank: int
    clicked: bool


@dataclass
class DocStats:
    impressions_w: float = 0.0
    clicks_w: float = 0.0
    n_impressions: int = 0
    n_clicks: int = 0


@dataclass(frozen=True)
class LearnedBoost:
    cluster_id: int
    doc_id: str
    boost: float
    n_impressions: int
    n_clicks: int


def aggregate(events: list[Event]) -> dict[int, dict[str, DocStats]]:
    """
    Sum position-weighted impressions and clicks per (cluster, doc).

    ``search_events`` stores impressions and clicks as **separate rows**:
    ``POST /v1/analytics/impressions`` writes ``clicked=False`` rows and
    ``POST /v1/analytics/click`` writes its own ``clicked=True`` row. So a
    click row counts as a click only, never as an extra impression. A
    click still implies the doc was shown, so impressions are raised to at
    least the click count. That covers a missed impression beacon and keeps
    every CTR within [0, 1].
    """
    out: dict[int, dict[str, DocStats]] = {}
    for ev in events:
        s = out.setdefault(ev.cluster_id, {}).setdefault(ev.doc_id, DocStats())
        w = position_weight(ev.rank)
        if ev.clicked:
            s.clicks_w += w
            s.n_clicks += 1
        else:
            s.impressions_w += w
            s.n_impressions += 1
    for docs in out.values():
        for s in docs.values():
            s.impressions_w = max(s.impressions_w, s.clicks_w)
            s.n_impressions = max(s.n_impressions, s.n_clicks)
    return out


def cluster_baseline(stats: dict[str, DocStats]) -> float:
    """Weighted CTR across every document shown for one cluster."""
    imp = sum(s.impressions_w for s in stats.values())
    if imp <= 0:
        return 0.0
    return sum(s.clicks_w for s in stats.values()) / imp


def shrunk_ctr(s: DocStats, baseline: float, prior_strength: float) -> float:
    return (s.clicks_w + prior_strength * baseline) / (s.impressions_w + prior_strength)


def boost_from_ctr(shrunk: float, baseline: float) -> float:
    """Lift above the baseline as a fraction of the headroom above it, in [0, 1]."""
    if baseline >= 1.0:
        return 0.0  # every impression was clicked; nothing distinguishes docs
    return min(1.0, max(0.0, (shrunk - baseline) / (1.0 - baseline)))


def compute_boosts(
    events: list[Event],
    prior_strength: float = DEFAULT_PRIOR_STRENGTH,
    min_boost: float = MIN_STORED_BOOST,
) -> list[LearnedBoost]:
    """
    Learned boosts for every (cluster, doc) with a boost of at least ``min_boost``.

    A cluster with no clicks has a baseline of 0, and every doc in it scores
    0: nothing is learned from it.
    """
    out: list[LearnedBoost] = []
    for cid, docs in aggregate(events).items():
        base = cluster_baseline(docs)
        if base <= 0:
            continue
        for doc_id, s in docs.items():
            b = boost_from_ctr(shrunk_ctr(s, base, prior_strength), base)
            if b >= min_boost:
                out.append(LearnedBoost(cid, doc_id, round(b, 6), s.n_impressions, s.n_clicks))
    out.sort(key=lambda lb: (lb.cluster_id, -lb.boost, lb.doc_id))
    return out
