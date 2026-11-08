# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Learned query-conditional boost — Case B of the trained re-scorer layer.

See ``docs/queryBoostImplementation.md`` for what runs, and
``docs/trainedRescorerDesign.md`` for the design and the reasoning behind it.

The package is split so each piece can be read, tested and replaced on its
own:

  clustering.py  pure: normalise queries, group them by intent
                 (embedding cosine), match a live query to a cluster
  scoring.py     pure: position-weighted clicks per (cluster, doc),
                 shrinkage toward the cluster's baseline CTR, boost in [0, 1]
  store.py       Postgres persistence + a short-TTL in-process cache
  trainer.py     orchestration: search_events → clusters → boosts → store
  serving.py     query time: resolve boosts for a query, build the
                 post-RRF hook that adds them to scores

Like Case A (``popularity_trainer.py``) it is fully decoupled: nothing in
``analytics_service`` or ``popularity_trainer`` imports it or is imported
by it. The only touch points with the rest of the code are the
``/v1/training/query-boost`` routes and one hook in each search router.
"""
