# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
/v1/training — trained ranking-signal models (Cases A and B of the trained
re-scorer layer, see docs/trainedRescorerDesign.md).

POST /v1/training/popularity?index_id=<slug-or-id>
    Train a browse-popularity model from search_events and write scores to
    Typesense — the trained-model counterpart to
    POST /v1/analytics/aggregate (which stays formula-based, unchanged).

POST   /v1/training/query-boost?index_id=<slug-or-id>
GET    /v1/training/query-boost?index_id=<slug-or-id>
DELETE /v1/training/query-boost?index_id=<slug-or-id>
    Train / inspect / remove the learned query-conditional boost (Case B,
    services/domain/query_boost, docs/queryBoostImplementation.md).
    DELETE is the per-index off switch: with no stored model, search
    applies no learned boost. It also turns off the index's schedule.

GET /v1/training/query-boost/schedule?index_id=<slug-or-id>
PUT /v1/training/query-boost/schedule?index_id=<slug-or-id>
    Per-index batch learning: a daily retrain at a set UTC hour over the
    last N days of events, run by services/domain/query_boost/scheduler.py.

Deliberately a separate router from routers/analytics.py: this feature is
decoupled from the existing analytics pipeline (see
popularity_trainer.py's module docstring) rather than added to it.

Popularity (Case A) is on-demand only: repeated training is triggered
externally (cron, k8s CronJob, or a manual call). The query boost (Case B)
can also be scheduled per index, see above.
"""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from isp.services.domain.query_boost import schedule, schedule_store

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


@router.post(
    "/training/query-boost",
    summary="Train the learned query-conditional boost (Case B) for an index",
)
async def train_query_boost(
    index_id: str = Query(..., description="Index slug or hex id"),
    days:     int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
):
    """
    Clusters non-empty search queries by intent, learns a per-(cluster, doc)
    boost from position-weighted clicks, and stores it for search to read.

    Returns trained=false (not an error) when there isn't enough query
    traffic yet; any previously stored model is then left in place.

    409 when a run for this index (manual or scheduled) is already in
    progress. trained=false with superseded=true when the model was deleted
    while this run trained; the deletion stands.
    """
    from isp.routers.indexes import _resolve_index
    from isp.services.domain.query_boost import trainer

    meta = await _resolve_index(index_id)
    if not meta:
        raise HTTPException(404, "Index not found")

    result = await trainer.train(meta.id, days)
    if result.get("busy"):
        raise HTTPException(409, "A training run for this index is already in progress")
    return {"index_id": meta.id, "slug": meta.slug, **result}


@router.get(
    "/training/query-boost",
    summary="Inspect the stored learned query boost for an index",
)
async def get_query_boost(index_id: str = Query(..., description="Index slug or hex id")):
    from isp.routers.indexes import _resolve_index
    from isp.services.domain.query_boost import store

    meta = await _resolve_index(index_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    return {"index_id": meta.id, "slug": meta.slug, **await store.model_summary(meta.id)}


@router.delete(
    "/training/query-boost",
    summary="Remove the learned query boost for an index (turns it off)",
)
async def delete_query_boost(index_id: str = Query(..., description="Index slug or hex id")):
    from isp.routers.indexes import _resolve_index
    from isp.services.domain.query_boost import store

    meta = await _resolve_index(index_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    # One transaction: model removed, generation bumped, schedule off. No run
    # already in flight, and no scheduled run at all, can publish over it.
    # A manual train started after this is a new, explicit request and does.
    disabled = await store.delete_model(meta.id, disable_schedule=True)
    return {"index_id": meta.id, "slug": meta.slug, "deleted": True,
            "schedule_disabled": disabled}


class QueryBoostScheduleUpdate(BaseModel):
    enabled:      bool
    days:         int = Field(default=schedule.DEFAULT_DAYS, ge=schedule.MIN_DAYS, le=schedule.MAX_DAYS,
                              description="Lookback window in days for each scheduled run")
    run_hour_utc: int = Field(default=schedule.DEFAULT_RUN_HOUR_UTC, ge=0, le=23,
                              description="Hour of day (UTC) the daily run starts")


@router.get(
    "/training/query-boost/schedule",
    summary="Get the batch-learning schedule for an index's query boost",
)
async def get_query_boost_schedule(index_id: str = Query(..., description="Index slug or hex id")):
    """
    The daily retrain schedule plus ``next_run_at`` and the last run's
    outcome. Returns a disabled default when none has been saved.
    """
    from isp.routers.indexes import _resolve_index

    meta = await _resolve_index(index_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    s = await schedule_store.get_schedule(meta.id)
    return {"slug": meta.slug, **schedule.to_dict(s, datetime.now(timezone.utc))}


@router.put(
    "/training/query-boost/schedule",
    summary="Set the batch-learning schedule for an index's query boost",
)
async def put_query_boost_schedule(
    body: QueryBoostScheduleUpdate,
    index_id: str = Query(..., description="Index slug or hex id"),
):
    """
    Turn daily retraining on or off and set its lookback window and hour.

    Changes take effect from the next scheduled hour; turning the schedule on
    does not train immediately. Use ``POST /training/query-boost`` for that.
    """
    from isp.routers.indexes import _resolve_index

    meta = await _resolve_index(index_id)
    if not meta:
        raise HTTPException(404, "Index not found")
    s = await schedule_store.save_schedule(
        meta.id, enabled=body.enabled, days=body.days, run_hour_utc=body.run_hour_utc
    )
    return {"slug": meta.slug, **schedule.to_dict(s, datetime.now(timezone.utc))}
