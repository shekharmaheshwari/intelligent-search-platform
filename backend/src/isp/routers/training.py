# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
/v1/training — trained ranking-signal models (Case A of the trained
re-scorer layer, see docs/trainedRescorerDesign.md).

POST /v1/training/popularity?index_id=<slug-or-id>
    Train a browse-popularity model from search_events and write scores to
    Typesense — the trained-model counterpart to
    POST /v1/analytics/aggregate (which stays formula-based, unchanged).

Deliberately a separate router from routers/analytics.py: this feature is
decoupled from the existing analytics pipeline (see
popularity_trainer.py's module docstring) rather than added to it.

On-demand only, same as /v1/analytics/aggregate — no scheduler exists in
this codebase, so repeated training is triggered externally (cron, k8s
CronJob, or a manual call), not by this service itself.
"""
from __future__ import annotations
from fastapi import APIRouter, Query

router = APIRouter(tags=["training"])


@router.post(
    "/training/popularity",
    summary="Train a browse-popularity model and write scores to Typesense",
)
async def train_popularity(
    index_id: str = Query(..., description="Index slug or hex id"),
    days:     int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
):
    """
    Trains a logistic-regression model on no-query (browse) search_events
    for the given index, scores every document, and writes the calibrated
    result back to the same `popularity` field
    POST /v1/analytics/aggregate writes to.

    Returns docs_scored=0 (not an error) when there isn't enough browse-event
    data yet to train on.
    """
    from isp.routers.indexes import _resolve_index
    from isp.services.domain import popularity_trainer

    meta = await _resolve_index(index_id)
    if not meta:
        return {"error": "index not found", "docs_scored": 0}

    result = await popularity_trainer.train_and_apply(meta.id, days)
    return {"index_id": meta.id, "slug": meta.slug, **result}
