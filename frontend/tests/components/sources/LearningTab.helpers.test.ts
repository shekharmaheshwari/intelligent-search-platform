// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import {
  isValidDays, hourLabel, hourOptions, relativeTime,
  nextRunText, statusLabel, trainResultMessage,
  isDirty, formAfterRefresh, trainErrorMessage, createLatestGate,
} from '../../../src/components/sources/LearningTab.helpers';

describe('isValidDays', () => {
  it.each([1, 30, 365])('accepts %s', n => expect(isValidDays(n)).toBe(true));
  it.each([0, 366, 2.5, NaN, null as unknown as number])('rejects %s', n => expect(isValidDays(n)).toBe(false));
});

describe('hourLabel', () => {
  it('shows only UTC when the viewer is on UTC', () => {
    expect(hourLabel(2, 0)).toBe('02:00 UTC');
  });
  it('adds local time with half-hour offsets (India, UTC+5:30)', () => {
    expect(hourLabel(2, -330)).toBe('02:00 UTC (07:30 local)');
  });
  it('wraps to the previous day west of UTC (US Pacific, UTC-8)', () => {
    expect(hourLabel(2, 480)).toBe('02:00 UTC (18:00 local)');
  });
  it('wraps past midnight east of UTC', () => {
    expect(hourLabel(23, -330)).toBe('23:00 UTC (04:30 local)');
  });
});

it('hourOptions covers all 24 hours in order', () => {
  const opts = hourOptions(0);
  expect(opts).toHaveLength(24);
  expect(opts[0]).toEqual({ value: 0, label: '00:00 UTC' });
  expect(opts[23].value).toBe(23);
});

describe('relativeTime', () => {
  const now = 1_000_000_000_000;
  const s = (deltaMin: number) => now / 1000 + deltaMin * 60;
  it('handles null', () => expect(relativeTime(null, now)).toBe('—'));
  it('minutes', () => expect(relativeTime(s(12), now)).toBe('in 12 min'));
  it('hours in the past', () => expect(relativeTime(s(-180), now)).toBe('3 h ago'));
  it('days beyond 48 h', () => expect(relativeTime(s(3 * 1440), now)).toBe('in 3 d'));
  it('just now', () => expect(relativeTime(s(0), now)).toBe('just now'));
});

describe('nextRunText', () => {
  const now = 1_000_000_000_000;
  it('not scheduled', () => expect(nextRunText(null, now)).toBe('Not scheduled'));
  it('overdue slot means due now', () => expect(nextRunText(now / 1000 - 60, now)).toMatch(/^Due now/));
  it('future slot is relative', () => expect(nextRunText(now / 1000 + 5 * 3600, now)).toBe('in 5 h'));
});

describe('statusLabel', () => {
  it.each([
    ['trained', 'Trained', 'ok'],
    ['skipped', 'Skipped', 'warn'],
    ['error', 'Failed', 'error'],
    ['running', 'Running', 'muted'],
    ['', 'Never run', 'muted'],
  ] as const)('%s', (status, label, tone) => {
    expect(statusLabel(status)).toEqual({ label, tone });
  });
});

describe('trainResultMessage', () => {
  const base = { index_id: 'i', slug: 's', n_queries: 0, n_boosts: 0 };
  it('trained', () => {
    expect(trainResultMessage({ ...base, trained: true, docs_boosted: 3, n_clusters: 2, n_events: 40 }))
      .toBe('Trained: 3 products boosted across 2 query groups from 40 events.');
  });
  it('not enough traffic', () => {
    expect(trainResultMessage({ ...base, trained: false, docs_boosted: 0, n_clusters: 0, n_events: 2 }))
      .toMatch(/Not enough query traffic yet \(2 events\)/);
  });
});

describe('background refresh keeps unsaved edits', () => {
  const saved = { enabled: false, days: 30, run_hour_utc: 2 };
  const fresh = { enabled: true, days: 14, run_hour_utc: 5 };

  it('isDirty', () => {
    expect(isDirty({ enabled: false, days: 30, runHourUtc: 2 }, saved)).toBe(false);
    expect(isDirty({ enabled: false, days: 31, runHourUtc: 2 }, saved)).toBe(true);
  });
  it('clean form follows the server', () => {
    expect(formAfterRefresh({ enabled: false, days: 30, runHourUtc: 2 }, saved, fresh))
      .toEqual({ enabled: true, days: 14, runHourUtc: 5 });
  });
  it('dirty form is left untouched', () => {
    const form = { enabled: false, days: 7, runHourUtc: 2 };
    expect(formAfterRefresh(form, saved, fresh)).toBe(form);
  });
});

describe('train-now messages', () => {
  const base = { index_id: 'i', slug: 's', n_queries: 0, n_boosts: 0, docs_boosted: 0, n_clusters: 0, n_events: 9 };
  it('superseded', () => {
    expect(trainResultMessage({ ...base, trained: false, superseded: true })).toMatch(/discarded/);
  });
  it('409 becomes a friendly message', () => {
    expect(trainErrorMessage(new Error('409 {"detail":"…"}'))).toMatch(/already in progress/);
  });
  it('other errors pass through', () => {
    expect(trainErrorMessage(new Error('500 boom'))).toBe('Error: 500 boom');
  });
});

describe('createLatestGate', () => {
  it('a poll that started before a save is dropped when it lands after it', () => {
    const gate = createLatestGate();
    const poll = gate.begin();          // GET sent
    const save = gate.begin();          // PUT sent before the GET returned
    expect(gate.isLatest(save)).toBe(true);   // PUT response applies
    expect(gate.isLatest(poll)).toBe(false);  // late GET response ignored
  });
  it('gates are independent', () => {
    const a = createLatestGate(), b = createLatestGate();
    const t = a.begin(); b.begin(); b.begin();
    expect(a.isLatest(t)).toBe(true);
  });
});
