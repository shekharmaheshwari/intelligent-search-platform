// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

/**
 * Pure helpers for LearningTab.svelte (query-boost batch-learning schedule).
 * No DOM or network access, so they are unit-tested directly.
 */
import type { QueryBoostRunStatus, QueryBoostSchedule, QueryBoostTrainResult } from '../../lib/types';

export const DAYS_MIN = 1;
export const DAYS_MAX = 365;

/** Mirrors the backend's validation (1–365 whole days). */
export function isValidDays(n: number): boolean {
  return Number.isInteger(n) && n >= DAYS_MIN && n <= DAYS_MAX;
}

const pad = (n: number) => String(n).padStart(2, '0');

/**
 * Label for a UTC run hour, with the viewer's local time alongside.
 *
 * @param offsetMinutes  ``Date#getTimezoneOffset()`` (minutes *behind* UTC,
 *                       so India is -330). Passed in to keep this testable.
 */
export function hourLabel(hourUtc: number, offsetMinutes: number): string {
  const utc = `${pad(hourUtc)}:00 UTC`;
  if (offsetMinutes === 0) return utc;
  const local = (((hourUtc * 60 - offsetMinutes) % 1440) + 1440) % 1440;
  return `${utc} (${pad(Math.floor(local / 60))}:${pad(local % 60)} local)`;
}

export function hourOptions(offsetMinutes: number): Array<{ value: number; label: string }> {
  return Array.from({ length: 24 }, (_, h) => ({ value: h, label: hourLabel(h, offsetMinutes) }));
}

/** "in 5 h", "12 min ago", "due now" — epoch seconds against ``nowMs``. */
export function relativeTime(epochSec: number | null, nowMs: number): string {
  if (epochSec == null) return '—';
  const diffMin = Math.round((epochSec * 1000 - nowMs) / 60000);
  const abs = Math.abs(diffMin);
  if (abs < 1) return 'just now';
  const span = abs < 60 ? `${abs} min` : abs < 48 * 60 ? `${Math.round(abs / 60)} h` : `${Math.round(abs / 1440)} d`;
  return diffMin > 0 ? `in ${span}` : `${span} ago`;
}

/** Text for the next scheduled run; a past time means it starts on the next check. */
export function nextRunText(nextRunAt: number | null, nowMs: number): string {
  if (nextRunAt == null) return 'Not scheduled';
  return nextRunAt * 1000 <= nowMs ? 'Due now (starts within a minute)' : relativeTime(nextRunAt, nowMs);
}

export function statusLabel(status: QueryBoostRunStatus): { label: string; tone: 'ok' | 'warn' | 'error' | 'muted' } {
  switch (status) {
    case 'trained': return { label: 'Trained', tone: 'ok' };
    case 'skipped': return { label: 'Skipped', tone: 'warn' };
    case 'error':   return { label: 'Failed', tone: 'error' };
    case 'running': return { label: 'Running', tone: 'muted' };
    default:        return { label: 'Never run', tone: 'muted' };
  }
}

/** One-line summary of an on-demand training result. */
export function trainResultMessage(r: QueryBoostTrainResult): string {
  if (r.superseded) {
    return 'The model was deleted or replaced while this run was training, so its result was discarded.';
  }
  if (!r.trained) {
    return `Not enough query traffic yet (${r.n_events} events). The previous model was kept.`;
  }
  return `Trained: ${r.docs_boosted} products boosted across ${r.n_clusters} query groups from ${r.n_events} events.`;
}

/** How often the tab re-reads schedule status while open. */
export const STATUS_POLL_MS = 30_000;

export interface ScheduleForm {
  enabled:    boolean;
  days:       number;
  runHourUtc: number;
}

type SavedSettings = Pick<QueryBoostSchedule, 'enabled' | 'days' | 'run_hour_utc'>;

/** True when the form differs from the saved schedule. */
export function isDirty(form: ScheduleForm, saved: SavedSettings): boolean {
  return form.enabled !== saved.enabled || form.days !== saved.days || form.runHourUtc !== saved.run_hour_utc;
}

/**
 * Form values after a background status refresh: take the server's settings
 * only when the user has no unsaved edits, otherwise keep what they typed.
 */
export function formAfterRefresh(form: ScheduleForm, prev: SavedSettings, fresh: SavedSettings): ScheduleForm {
  return isDirty(form, prev)
    ? form
    : { enabled: fresh.enabled, days: fresh.days, runHourUtc: fresh.run_hour_utc };
}

/** User-facing text for a failed "Train now". */
export function trainErrorMessage(err: unknown): string {
  const s = String(err);
  return /\b409\b/.test(s)
    ? 'A training run for this index is already in progress. Try again when it finishes.'
    : s;
}

/**
 * Latest-request-wins gate. Each request takes a token with ``begin()``; its
 * response may be applied only while ``isLatest(token)``. A status poll that
 * started before a save therefore can't land after it and restore older
 * values.
 */
export function createLatestGate(): { begin(): number; isLatest(token: number): boolean } {
  let seq = 0;
  return {
    begin: () => ++seq,
    isLatest: (token: number) => token === seq,
  };
}
