// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

/** Last outcome of a scheduled query-boost run ('' when it has never run). */
export type QueryBoostRunStatus = '' | 'running' | 'trained' | 'skipped' | 'error';

/** GET/PUT /v1/training/query-boost/schedule response. Times are epoch seconds. */
export interface QueryBoostSchedule {
  index_id:         string;
  slug:             string;
  enabled:          boolean;
  days:             number;
  run_hour_utc:     number;
  enabled_at:       number | null;
  last_run_at:      number | null;
  last_finished_at: number | null;
  last_status:      QueryBoostRunStatus;
  last_message:     string;
  next_run_at:      number | null;
}

/** PUT /v1/training/query-boost/schedule body. */
export interface QueryBoostScheduleUpdate {
  enabled:      boolean;
  days:         number;
  run_hour_utc: number;
}

/** POST /v1/training/query-boost response (an on-demand run). */
export interface QueryBoostTrainResult {
  index_id:     string;
  slug:         string;
  trained:      boolean;
  n_events:     number;
  n_queries:    number;
  n_clusters:   number;
  n_boosts:     number;
  docs_boosted: number;
  /** Another run held the per-index lock (the API answers 409 instead). */
  busy?:        boolean;
  /** The model was deleted or replaced during this run; result discarded. */
  superseded?:  boolean;
}
