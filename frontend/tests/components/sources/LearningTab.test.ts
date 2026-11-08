// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/svelte';
import type { QueryBoostSchedule } from '../../../src/lib/types';

const getSchedule = vi.fn();
vi.mock('../../../src/lib/api', () => ({
  api: {
    getQueryBoostSchedule: (...a: unknown[]) => getSchedule(...a),
    saveQueryBoostSchedule: vi.fn(),
    trainQueryBoostNow: vi.fn(),
  },
}));

import LearningTab from '../../../src/components/sources/LearningTab.svelte';

const flush = () => new Promise(r => setTimeout(r, 0));

const schedule: QueryBoostSchedule = {
  index_id: 'idx', slug: 's', enabled: false, days: 30, run_hour_utc: 2,
  enabled_at: null, last_run_at: null, last_finished_at: null,
  last_status: '', last_message: '', next_run_at: null,
};

describe('LearningTab polling lifecycle', () => {
  let setIntervalSpy: ReturnType<typeof vi.spyOn>;
  let addListenerSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    getSchedule.mockReset();
    setIntervalSpy = vi.spyOn(globalThis, 'setInterval');
    addListenerSpy = vi.spyOn(document, 'addEventListener');
  });
  afterEach(() => vi.restoreAllMocks());

  const visibilityListeners = () =>
    addListenerSpy.mock.calls.filter(([type]) => type === 'visibilitychange').length;

  it('leaving the tab before the first load finishes installs no polling', async () => {
    // Regression: cleanup ran before the interval existed; the resumed
    // onMount then installed a poll and listener nothing ever removed.
    let resolve!: (s: QueryBoostSchedule) => void;
    getSchedule.mockReturnValue(new Promise(r => (resolve = r)));

    const { unmount } = render(LearningTab, { indexId: 'idx' });
    expect(getSchedule).toHaveBeenCalledTimes(1);   // onMount really ran
    unmount();
    resolve(schedule);
    await flush();

    expect(setIntervalSpy).not.toHaveBeenCalled();
    expect(visibilityListeners()).toBe(0);
  });

  it('normal mount polls, and unmount removes both the poll and the listener', async () => {
    getSchedule.mockResolvedValue(schedule);
    const clearSpy = vi.spyOn(globalThis, 'clearInterval');
    const removeSpy = vi.spyOn(document, 'removeEventListener');

    const { unmount, getByText } = render(LearningTab, { indexId: 'idx' });
    await flush();
    getByText('Daily batch learning');
    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
    expect(visibilityListeners()).toBe(1);

    unmount();
    expect(clearSpy).toHaveBeenCalledWith(setIntervalSpy.mock.results[0].value);
    expect(removeSpy.mock.calls.some(([type]) => type === 'visibilitychange')).toBe(true);
  });
});
