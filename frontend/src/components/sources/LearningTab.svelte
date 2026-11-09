<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0

  Learning tab: per-index batch-learning schedule for the learned query boost.
  Daily retrain toggle, lookback window, run hour (UTC), last-run status,
  and an on-demand "Train now".
-->

<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { api } from '../../lib/api';
  import type { QueryBoostSchedule } from '../../lib/types';
  import {
    DAYS_MIN, DAYS_MAX, isValidDays, hourOptions,
    relativeTime, nextRunText, statusLabel, trainResultMessage,
    STATUS_POLL_MS, isDirty, formAfterRefresh, trainErrorMessage, createLatestGate,
  } from './LearningTab.helpers';

  export let indexId: string;

  const hours = hourOptions(new Date().getTimezoneOffset());
  // Every schedule GET/PUT takes a token; only the newest response is applied.
  const gate = createLatestGate();

  let loaded: QueryBoostSchedule | null = null;
  let enabled = false;
  let days = 30;
  let runHourUtc = 2;

  let loading = true;
  let saving = false;
  let training = false;
  let error = '';
  let saveMsg = '';
  let trainMsg = '';
  let now = Date.now();

  $: daysValid = isValidDays(days);
  $: dirty = !!loaded && isDirty({ enabled, days, runHourUtc }, loaded);
  $: status = statusLabel(loaded?.last_status ?? '');

  function apply(s: QueryBoostSchedule): void {
    loaded = s;
    enabled = s.enabled;
    days = s.days;
    runHourUtc = s.run_hour_utc;
    now = Date.now();
  }

  /**
   * Background status refresh. A scheduled run can finish while the tab is
   * open, so status and next-run time are re-read periodically. Unsaved
   * edits are never overwritten (formAfterRefresh), and a response that a
   * later save overtook is dropped (gate). Errors here are ignored; the next
   * poll retries.
   */
  async function refresh(): Promise<void> {
    if (!loaded || saving) return;
    const token = gate.begin();
    try {
      const fresh = await api.getQueryBoostSchedule(indexId);
      if (!gate.isLatest(token) || !loaded) return;
      ({ enabled, days, runHourUtc } = formAfterRefresh({ enabled, days, runHourUtc }, loaded, fresh));
      loaded = fresh;
    } catch {
      /* transient; keep showing the last known status */
    }
    now = Date.now();
  }

  let poll: ReturnType<typeof setInterval> | undefined;
  // Set on destroy. The initial GET can resolve after the tab is left; it must
  // then not install polling that nothing would ever remove.
  let destroyed = false;

  function onVisibility(): void {
    if (!destroyed && document.visibilityState === 'visible') refresh();
  }

  onMount(async () => {
    const token = gate.begin();
    try {
      const s = await api.getQueryBoostSchedule(indexId);
      if (gate.isLatest(token) && !destroyed) apply(s);
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
    if (destroyed) return;
    poll = setInterval(() => {
      if (document.visibilityState === 'visible') refresh();
    }, STATUS_POLL_MS);
    document.addEventListener('visibilitychange', onVisibility);
  });

  onDestroy(() => {
    destroyed = true;
    if (poll) clearInterval(poll);
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisibility);
  });

  async function save(): Promise<void> {
    if (!daysValid) return;
    saving = true; error = ''; saveMsg = '';
    // Taking a token here invalidates any poll still in flight. Polls don't
    // start while saving, so this response stays the latest.
    const token = gate.begin();
    try {
      const s = await api.saveQueryBoostSchedule(indexId, { enabled, days, run_hour_utc: runHourUtc });
      if (gate.isLatest(token)) apply(s);
      saveMsg = 'Saved';
      setTimeout(() => (saveMsg = ''), 2000);
    } catch (e) {
      error = String(e);
    } finally {
      saving = false;
    }
  }

  async function trainNow(): Promise<void> {
    if (!daysValid) return;
    training = true; error = ''; trainMsg = '';
    try {
      trainMsg = trainResultMessage(await api.trainQueryBoostNow(indexId, days));
    } catch (e) {
      trainMsg = trainErrorMessage(e);
    } finally {
      training = false;
      refresh();
    }
  }
</script>

<div class="learning-tab">
  <div class="tab-header">
    <div>
      <h2 class="tab-title">Learning</h2>
      <p class="tab-desc">
        Retrains the <strong>learned query boost</strong> for this index from recent search clicks,
        so products shoppers pick for a kind of query rank a little higher for similar queries.
        Training reads only the last <strong>N days</strong> of events.
      </p>
    </div>
    <button class="save-btn" on:click={save} disabled={saving || !dirty || !daysValid}>
      {saving ? 'Saving…' : saveMsg ? '✓ Saved' : 'Save schedule'}
    </button>
  </div>

  {#if error}<div class="alert-error">{error}</div>{/if}

  {#if loading}
    <p class="loading">Loading…</p>
  {:else if loaded}
    <div class="card">
      <div class="row">
        <div class="row-text">
          <span class="row-title">Daily batch learning</span>
          <span class="row-sub">Retrain once a day at the hour below. Turning this on starts from the next scheduled hour.</span>
        </div>
        <label class="toggle" title={enabled ? 'Enabled' : 'Disabled'}>
          <input type="checkbox" bind:checked={enabled} aria-label="Daily batch learning" />
          <span class="toggle-track"></span>
        </label>
      </div>

      <div class="fields" class:fields-off={!enabled}>
        <label class="field">
          <span class="field-label">Learn from the last</span>
          <span class="field-input">
            <input
              class="num-input"
              class:invalid={!daysValid}
              type="number"
              min={DAYS_MIN}
              max={DAYS_MAX}
              step="1"
              bind:value={days}
            />
            <span class="field-unit">days</span>
          </span>
          {#if !daysValid}<span class="field-error">Enter {DAYS_MIN}–{DAYS_MAX} whole days.</span>{/if}
        </label>

        <label class="field">
          <span class="field-label">Run at</span>
          <select class="select" bind:value={runHourUtc}>
            {#each hours as h}<option value={h.value}>{h.label}</option>{/each}
          </select>
        </label>
      </div>
    </div>

    <div class="card">
      <span class="row-title">Status</span>
      <dl class="status">
        <dt>Next run</dt>
        <dd>{loaded.enabled ? nextRunText(loaded.next_run_at, now) : 'Not scheduled'}</dd>

        <dt>Last scheduled run</dt>
        <dd>
          <span class="pill tone-{status.tone}">{status.label}</span>
          {#if loaded.last_run_at}<span class="muted">{relativeTime(loaded.last_run_at, now)}</span>{/if}
        </dd>

        {#if loaded.last_message}
          <dt>Result</dt>
          <dd class="muted">{loaded.last_message}</dd>
        {/if}
      </dl>
      {#if dirty}<p class="muted small">Unsaved changes. Status reflects the saved schedule.</p>{/if}
    </div>

    <div class="card row">
      <div class="row-text">
        <span class="row-title">Train now</span>
        <span class="row-sub">Runs once immediately with the {daysValid ? days : '—'}-day window, without changing the schedule.</span>
        {#if trainMsg}<span class="train-msg">{trainMsg}</span>{/if}
      </div>
      <button class="secondary-btn" on:click={trainNow} disabled={training || !daysValid}>
        {training ? 'Training…' : 'Train now'}
      </button>
    </div>
  {/if}
</div>

<style>
  .learning-tab { display: flex; flex-direction: column; gap: 16px; max-width: 680px; }

  .tab-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  .tab-title { font-size: 15px; font-weight: 600; color: var(--text); margin-bottom: 4px; }
  .tab-desc { font-size: 13px; color: var(--text-5); line-height: 1.5; max-width: 520px; }
  .tab-desc strong { color: var(--text-4); }

  .save-btn {
    flex-shrink: 0; background: var(--accent); color: #fff; border: none;
    border-radius: 6px; padding: 7px 16px; font-size: 13px; font-weight: 500;
    cursor: pointer; white-space: nowrap;
  }
  .save-btn:hover:not(:disabled) { opacity: 0.88; }
  .save-btn:disabled { opacity: 0.5; cursor: default; }

  .secondary-btn {
    flex-shrink: 0; background: var(--bg); color: var(--text-2);
    border: 1px solid var(--border-2); border-radius: 6px; padding: 7px 14px;
    font-size: 13px; cursor: pointer; white-space: nowrap;
  }
  .secondary-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .secondary-btn:disabled { opacity: 0.5; cursor: default; }

  .alert-error {
    background: rgba(220,50,50,.08); border: 1px solid rgba(220,50,50,.3);
    color: #f87171; border-radius: 6px; padding: 10px 14px; font-size: 13px;
  }
  .loading { color: var(--text-5); font-size: 13px; }

  .card {
    display: flex; flex-direction: column; gap: 14px;
    padding: 14px 16px; background: var(--bg-2);
    border: 1px solid var(--border); border-radius: 8px;
  }
  .card.row, .row { flex-direction: row; align-items: center; justify-content: space-between; gap: 16px; }
  .row { display: flex; }
  .row-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .row-title { font-size: 13px; font-weight: 600; color: var(--text-2); }
  .row-sub { font-size: 12px; color: var(--text-5); line-height: 1.4; }

  .fields { display: flex; gap: 20px; flex-wrap: wrap; transition: opacity 0.15s; }
  .fields-off { opacity: 0.55; }
  .field { display: flex; flex-direction: column; gap: 6px; }
  .field-label { font-size: 11px; font-weight: 600; color: var(--text-4); text-transform: uppercase; letter-spacing: 0.04em; }
  .field-input { display: flex; align-items: center; gap: 8px; }
  .field-unit { font-size: 12px; color: var(--text-4); }
  .field-error { font-size: 11px; color: #f87171; }

  .num-input, .select {
    background: var(--bg); border: 1px solid var(--border-2); border-radius: 5px;
    padding: 5px 8px; font-size: 13px; color: var(--text);
  }
  .num-input { width: 80px; font-variant-numeric: tabular-nums; }
  .num-input.invalid { border-color: #f87171; }
  .num-input:focus, .select:focus { outline: none; border-color: var(--accent); }

  .status {
    display: grid; grid-template-columns: max-content 1fr; gap: 8px 16px;
    margin: 0; font-size: 13px;
  }
  .status dt { color: var(--text-4); }
  .status dd { margin: 0; color: var(--text-2); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

  .pill {
    font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px;
    background: rgba(100,100,100,.12); color: var(--text-4);
  }
  .tone-ok    { background: rgba(34,197,94,.12);  color: #16a34a; }
  .tone-warn  { background: rgba(234,179,8,.14);  color: #b45309; }
  .tone-error { background: rgba(220,50,50,.12);  color: #dc2626; }

  .muted { color: var(--text-5); }
  .small { font-size: 12px; margin: 0; }
  .train-msg { font-size: 12px; color: var(--text-3); margin-top: 6px; }

  /* Toggle (same as BoostRulesTab) */
  .toggle { position: relative; display: inline-flex; cursor: pointer; flex-shrink: 0; }
  .toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
  .toggle-track {
    width: 32px; height: 18px; background: var(--border); border-radius: 9px;
    transition: background 0.15s; position: relative;
  }
  .toggle-track::after {
    content: ''; position: absolute; top: 3px; left: 3px;
    width: 12px; height: 12px; background: #fff; border-radius: 50%;
    transition: transform 0.15s;
  }
  .toggle input:checked + .toggle-track { background: var(--accent); }
  .toggle input:checked + .toggle-track::after { transform: translateX(14px); }
  .toggle input:focus-visible + .toggle-track { outline: 2px solid var(--accent); outline-offset: 2px; }

  @media (max-width: 600px) {
    .tab-header, .card.row { flex-direction: column; align-items: stretch; }
  }
</style>
