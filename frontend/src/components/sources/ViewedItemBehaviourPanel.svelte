<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  import type { ViewedItemPolicy, ItemPolicy } from '../../lib/types';

  export let policy: ViewedItemPolicy;
  export let onChange: (p: ViewedItemPolicy) => void;

  const MODES: Array<{ val: ItemPolicy['mode']; label: string; desc: string }> = [
    { val: 'penalise', label: 'Penalise', desc: 'Subtract — push down in results' },
    { val: 'neutral',  label: 'Neutral',  desc: 'No change' },
    { val: 'boost',    label: 'Boost',    desc: 'Add — surface in results' },
  ];

  const SECTIONS: Array<{ key: keyof ViewedItemPolicy; title: string; sub: string }> = [
    {
      key:   'viewed',
      title: 'Viewed (not purchased)',
      sub:   'Active interest — customer has seen this but not bought it yet.',
    },
    {
      key:   'purchased',
      title: 'Purchased',
      sub:   'Need fulfilled — customer completed a purchase (durable goods).',
    },
  ];

  function onMode(key: keyof ViewedItemPolicy, e: Event) {
    const mode = (e.currentTarget as HTMLInputElement).value as ItemPolicy['mode'];
    onChange({ ...policy, [key]: { ...policy[key], mode } });
  }

  function onValue(key: keyof ViewedItemPolicy, e: Event) {
    const n = parseFloat((e.currentTarget as HTMLInputElement).value);
    if (!isNaN(n) && n >= 0 && n <= 1) {
      onChange({ ...policy, [key]: { ...policy[key], value: n } });
    }
  }
</script>

<div class="vib-section">
  <h4 class="vib-title">Item Interaction Behaviour</h4>
  <p class="vib-sub">
    Configure how viewed and purchased items are scored in personalised results.
  </p>

  <div class="sub-sections">
    {#each SECTIONS as s}
      {@const sub = policy[s.key]}
      <div class="sub-block">
        <div class="sub-header">
          <span class="sub-title">{s.title}</span>
          <span class="sub-desc">{s.sub}</span>
        </div>

        <div class="mode-group">
          {#each MODES as m}
            <label class="mode-option" class:mode-active={sub.mode === m.val}>
              <input
                type="radio"
                name="vib-{s.key}"
                value={m.val}
                checked={sub.mode === m.val}
                on:change={e => onMode(s.key, e)}
              />
              <span class="mode-label">{m.label}</span>
              <span class="mode-desc">{m.desc}</span>
            </label>
          {/each}
        </div>

        {#if sub.mode !== 'neutral'}
          <div class="value-row">
            <span class="value-sign">{sub.mode === 'boost' ? '+' : '−'}</span>
            <input
              class="value-input"
              type="number"
              min="0"
              max="1"
              step="0.01"
              value={sub.value}
              on:input={e => onValue(s.key, e)}
            />
            <span class="value-hint">score adjustment per item</span>
          </div>
        {/if}
      </div>
    {/each}
  </div>
</div>

<style>
  .vib-section {
    border-top: 1px solid var(--border);
    padding-top: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .vib-title {
    margin: 0;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-2);
  }

  .vib-sub {
    margin: 0;
    font-size: 12px;
    color: var(--text-5);
    line-height: 1.5;
  }

  .sub-sections {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .sub-block {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 14px 16px;
    background: var(--bg-2);
    border: 1px solid var(--border);
    border-radius: 8px;
  }

  .sub-header { display: flex; flex-direction: column; gap: 2px; }

  .sub-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-3);
  }

  .sub-desc {
    font-size: 11px;
    color: var(--text-5);
    line-height: 1.4;
  }

  .mode-group {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .mode-option {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 110px;
    max-width: 180px;
    padding: 8px 10px;
    border: 1px solid var(--border-2);
    border-radius: 7px;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
    background: var(--bg);
  }

  .mode-option input[type="radio"] { display: none; }

  .mode-active {
    border-color: var(--accent);
    background: var(--accent-dim);
  }

  .mode-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-2);
  }

  .mode-active .mode-label { color: var(--accent); }

  .mode-desc {
    font-size: 10px;
    color: var(--text-5);
    line-height: 1.4;
  }

  .value-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .value-sign {
    font-size: 16px;
    font-weight: 700;
    color: var(--text-3);
    width: 12px;
    text-align: center;
  }

  .value-input {
    width: 72px;
    background: var(--bg);
    border: 1px solid var(--border-2);
    border-radius: 5px;
    padding: 4px 7px;
    font-size: 12px;
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }

  .value-input:focus { outline: none; border-color: var(--accent); }

  .value-hint {
    font-size: 11px;
    color: var(--text-5);
  }
</style>
