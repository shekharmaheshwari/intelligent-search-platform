<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../../lib/api';
  import type { BoostRule, BoostCriterion } from '../../lib/types';
  import DocPickerModal from './DocPickerModal.svelte';

  export let indexId: string;

  const OPERATORS = [
    { value: 'equals',   label: 'Equals' },
    { value: 'contains', label: 'Contains' },
    { value: 'regex',    label: 'Matches Regex' },
  ] as const;

  const DANGER_THRESHOLD = 0.5;

  let rules: BoostRule[] = [];
  let fields: string[]   = [];
  let loading  = true;
  let saving   = false;
  let error    = '';
  let saveMsg  = '';

  // Doc picker state — tracks which (ruleId, targetIdx) is being browsed.
  let pickerRuleId: string | null  = null;
  let pickerTargetIdx: number | null = null;
  // title lookup: ruleId → targetIdx → title (populated when a doc is picked via Browse)
  let pickerTitles: Record<string, Record<number, string>> = {};

  onMount(async () => {
    try {
      const [rRes, fRes] = await Promise.all([
        api.getBoostRules(indexId),
        api.getBoostRuleFields(indexId),
      ]);
      rules  = (rRes.rules ?? []).map((r: any) => ({
        ...r,
        trigger: r.trigger ?? r.criteria ?? [],
        targets: r.targets ?? [],
      }));
      fields = fRes.fields ?? [];
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  });

  function nanoid(): string {
    return Math.random().toString(36).slice(2, 10);
  }

  function blankCriterion(forTrigger: boolean): BoostCriterion {
    return { field: forTrigger ? '__search_term__' : 'id', operator: 'equals', value: '' };
  }

  function addRule(): void {
    rules = [...rules, {
      id:      nanoid(),
      label:   'New rule',
      enabled: true,
      boost:   0.10,
      trigger: [blankCriterion(true)],
      targets: [],
    }];
  }

  function removeRule(id: string): void {
    rules = rules.filter(r => r.id !== id);
  }

  function updateRule(id: string, patch: Partial<BoostRule>): void {
    rules = rules.map(r => r.id === id ? { ...r, ...patch } : r);
  }

  function addCriterion(ruleId: string, section: 'trigger' | 'targets'): void {
    rules = rules.map(r => r.id === ruleId
      ? { ...r, [section]: [...r[section], blankCriterion(section === 'trigger')] }
      : r
    );
  }

  function removeCriterion(ruleId: string, section: 'trigger' | 'targets', idx: number): void {
    rules = rules.map(r => r.id === ruleId
      ? { ...r, [section]: r[section].filter((_, i) => i !== idx) }
      : r
    );
  }

  function updateCriterion(ruleId: string, section: 'trigger' | 'targets', idx: number, patch: Partial<BoostCriterion>): void {
    rules = rules.map(r => r.id === ruleId
      ? { ...r, [section]: r[section].map((c, i) => i === idx ? { ...c, ...patch } : c) }
      : r
    );
  }

  function updateOperator(ruleId: string, section: 'trigger' | 'targets', idx: number, value: string): void {
    updateCriterion(ruleId, section, idx, { operator: value as BoostCriterion['operator'] });
  }

  function openPicker(ruleId: string, idx: number): void {
    pickerRuleId    = ruleId;
    pickerTargetIdx = idx;
  }

  function onPickerSelect(e: CustomEvent<{ id: string; title: string }>): void {
    if (pickerRuleId === null || pickerTargetIdx === null) return;
    const { id, title } = e.detail;
    updateCriterion(pickerRuleId, 'targets', pickerTargetIdx, { value: id });
    pickerTitles = {
      ...pickerTitles,
      [pickerRuleId]: { ...(pickerTitles[pickerRuleId] ?? {}), [pickerTargetIdx]: title },
    };
    pickerRuleId    = null;
    pickerTargetIdx = null;
  }

  function onPickerClose(): void {
    pickerRuleId    = null;
    pickerTargetIdx = null;
  }

  function getPickedTitle(ruleId: string, idx: number): string | null {
    return pickerTitles[ruleId]?.[idx] ?? null;
  }

  function ruleMode(rule: BoostRule): 'search' | 'browse' {
    return rule.trigger.some(c => c.field === '__search_term__') ? 'search' : 'browse';
  }

  function formatBoost(v: number): string {
    return `+${v.toFixed(2)}`;
  }

  function boostZone(v: number): 'normal' | 'danger' {
    return v >= DANGER_THRESHOLD ? 'danger' : 'normal';
  }

  async function save(): Promise<void> {
    saving  = true;
    error   = '';
    saveMsg = '';
    try {
      const res = await api.updateBoostRules(indexId, rules);
      rules   = res.rules;
      saveMsg = 'Saved';
      setTimeout(() => (saveMsg = ''), 2000);
    } catch (e) {
      error = String(e);
    } finally {
      saving = false;
    }
  }
</script>

<div class="boost-tab">
  <div class="tab-header">
    <div>
      <h2 class="tab-title">Boost Rules</h2>
      <p class="tab-desc">Each rule has a <strong>Trigger</strong> (when it fires — all conditions AND-ed) and <strong>Boost targets</strong> (which products get boosted — any condition OR-ed). Rules with a search-term trigger fire during search; others fire during browse.</p>
    </div>
    <button class="save-btn" on:click={save} disabled={saving}>
      {saving ? 'Saving…' : saveMsg ? '✓ Saved' : 'Save rules'}
    </button>
  </div>

  {#if error}
    <div class="alert-error">{error}</div>
  {/if}

  {#if loading}
    <div class="loading">Loading…</div>
  {:else if rules.length === 0}
    <div class="empty">
      <p>No boost rules yet. Create your first rule to start boosting products.</p>
      <button class="add-rule-btn" on:click={addRule}>+ Add rule block</button>
    </div>
  {:else}
    <div class="rules-list">
      {#each rules as rule (rule.id)}
        <div class="rule-card" class:rule-disabled={!rule.enabled}>

          <!-- Rule header -->
          <div class="rule-head">
            <input
              class="rule-label-input"
              type="text"
              bind:value={rule.label}
              placeholder="Rule name…"
              on:input={() => updateRule(rule.id, { label: rule.label })}
            />
            <span class="mode-badge" class:mode-search={ruleMode(rule) === 'search'}>
              {ruleMode(rule) === 'search' ? 'On search' : 'On browse'}
            </span>
            <label class="toggle" title={rule.enabled ? 'Enabled' : 'Disabled'}>
              <input
                type="checkbox"
                checked={rule.enabled}
                on:change={(e) => updateRule(rule.id, { enabled: e.currentTarget.checked })}
              />
              <span class="toggle-track"></span>
            </label>
            <button class="rule-delete-btn" on:click={() => removeRule(rule.id)} title="Delete rule">✕</button>
          </div>

          <div class="rule-body">
            <!-- Trigger section -->
            <div class="section">
              <div class="section-head">
                <span class="section-title">Trigger</span>
                <span class="logic-badge and-badge">ALL match (AND)</span>
                <span class="section-hint">When to fire this rule</span>
              </div>
              {#each rule.trigger as c, idx}
                <div class="criterion-row">
                  <!-- Field: trigger allows __search_term__ + schema fields -->
                  <select
                    class="crit-select"
                    value={c.field}
                    on:change={(e) => updateCriterion(rule.id, 'trigger', idx, { field: e.currentTarget.value })}
                  >
                    <option value="__search_term__">Search term</option>
                    {#each fields as f}
                      <option value={f}>{f}</option>
                    {/each}
                  </select>
                  <select
                    class="crit-select crit-op"
                    value={c.operator}
                    on:change={(e) => updateOperator(rule.id, 'trigger', idx, e.currentTarget.value)}
                  >
                    {#each OPERATORS as op}
                      <option value={op.value}>{op.label}</option>
                    {/each}
                  </select>
                  <input
                    class="crit-value"
                    type="text"
                    value={c.value}
                    placeholder={c.operator === 'regex' ? '^apple.*' : c.field === '__search_term__' ? 'e.g. apple' : 'Enter value…'}
                    on:input={(e) => updateCriterion(rule.id, 'trigger', idx, { value: e.currentTarget.value })}
                  />
                  {#if rule.trigger.length > 1}
                    <button class="crit-remove" on:click={() => removeCriterion(rule.id, 'trigger', idx)}>✕</button>
                  {/if}
                </div>
              {/each}
              <button class="add-criterion-btn" on:click={() => addCriterion(rule.id, 'trigger')}>+ Add trigger condition</button>
            </div>

            <div class="section-divider">→ boost</div>

            <!-- Targets section -->
            <div class="section">
              <div class="section-head">
                <span class="section-title">Boost targets</span>
                <span class="logic-badge or-badge">ANY match (OR)</span>
                <span class="section-hint">Which products to boost{rule.targets.length === 0 ? ' — empty means all products' : ''}</span>
              </div>
              {#if rule.targets.length === 0}
                <p class="targets-empty">All products matching the trigger will be boosted. Add a target to narrow it down.</p>
              {/if}
              {#each rule.targets as c, idx}
                <div class="criterion-row">
                  <!-- Field: targets use schema fields only (no search term) -->
                  <select
                    class="crit-select"
                    value={c.field}
                    on:change={(e) => updateCriterion(rule.id, 'targets', idx, { field: e.currentTarget.value })}
                  >
                    <option value="id">id (Browse to pick)</option>
                    {#each fields as f}
                      <option value={f}>{f}</option>
                    {/each}
                  </select>
                  <select
                    class="crit-select crit-op"
                    value={c.operator}
                    on:change={(e) => updateOperator(rule.id, 'targets', idx, e.currentTarget.value)}
                  >
                    {#each OPERATORS as op}
                      <option value={op.value}>{op.label}</option>
                    {/each}
                  </select>
                  {#if c.field === 'id'}
                    <div class="id-picker-row">
                      {#if c.value}
                        <span class="id-chip">
                          <span class="id-chip-inner">
                            {#if getPickedTitle(rule.id, idx)}
                              <span class="id-chip-title">{getPickedTitle(rule.id, idx)}</span>
                            {/if}
                            <span class="id-chip-id">{c.value}</span>
                          </span>
                          <button class="id-chip-clear" on:click={() => updateCriterion(rule.id, 'targets', idx, { value: '' })}>✕</button>
                        </span>
                      {:else}
                        <span class="id-chip-empty">No document selected</span>
                      {/if}
                      <button class="browse-btn" on:click={() => openPicker(rule.id, idx)}>Browse</button>
                    </div>
                  {:else}
                    <input
                      class="crit-value"
                      type="text"
                      value={c.value}
                      placeholder={c.operator === 'regex' ? '^PROD-\\d{4}$' : 'Enter value…'}
                      on:input={(e) => updateCriterion(rule.id, 'targets', idx, { value: e.currentTarget.value })}
                    />
                  {/if}
                  <button class="crit-remove" on:click={() => removeCriterion(rule.id, 'targets', idx)}>✕</button>
                </div>
              {/each}
              <button class="add-criterion-btn" on:click={() => addCriterion(rule.id, 'targets')}>+ Add target condition</button>
            </div>
          </div>

          <!-- Boost slider -->
          <div class="boost-section" class:boost-danger={boostZone(rule.boost) === 'danger'}>
            <div class="boost-row">
              <span class="boost-label">Boost</span>
              <input
                class="boost-slider"
                class:slider-danger={boostZone(rule.boost) === 'danger'}
                type="range"
                min="0" max="1" step="0.01"
                value={rule.boost}
                on:input={(e) => updateRule(rule.id, { boost: parseFloat(e.currentTarget.value) })}
              />
              <span class="boost-value" class:boost-value-danger={boostZone(rule.boost) === 'danger'}>
                {#if boostZone(rule.boost) === 'danger'}⚠{/if}
                {formatBoost(rule.boost)}
              </span>
            </div>
            <p class="boost-hint">
              {#if boostZone(rule.boost) === 'danger'}
                Boosts ≥ +0.50 aggressively override keyword and semantic relevance. Matching products may jump to the top even for loosely related queries.
              {:else}
                Applies a preference nudge. Natural search relevance remains intact.
              {/if}
            </p>
          </div>

        </div>
      {/each}
    </div>

    <button class="add-rule-btn" on:click={addRule}>+ Add rule block</button>
  {/if}
</div>

{#if pickerRuleId !== null}
  <DocPickerModal
    {indexId}
    selectedIds={[]}
    on:select={onPickerSelect}
    on:close={onPickerClose}
  />
{/if}

<style>
  .boost-tab { display: flex; flex-direction: column; gap: 20px; }

  .tab-header {
    display: flex; align-items: flex-start; justify-content: space-between; gap: 16px;
  }
  .tab-title { font-size: 15px; font-weight: 600; color: var(--text); margin-bottom: 4px; }
  .tab-desc { font-size: 13px; color: var(--text-5); line-height: 1.5; max-width: 580px; }
  .tab-desc strong { color: var(--text-4); }

  .save-btn {
    flex-shrink: 0; background: var(--accent); color: #fff; border: none;
    border-radius: 6px; padding: 7px 16px; font-size: 13px; font-weight: 500;
    cursor: pointer; white-space: nowrap;
  }
  .save-btn:hover { opacity: 0.88; }
  .save-btn:disabled { opacity: 0.5; cursor: default; }

  .alert-error {
    background: rgba(220,50,50,.08); border: 1px solid rgba(220,50,50,.3);
    color: #f87171; border-radius: 6px; padding: 10px 14px; font-size: 13px;
  }

  .loading { color: var(--text-5); font-size: 13px; }

  .empty { text-align: center; padding: 40px 0; color: var(--text-5); font-size: 13px; }
  .empty p { margin-bottom: 16px; }

  .rules-list { display: flex; flex-direction: column; gap: 16px; }

  /* Rule card */
  .rule-card {
    border: 1px solid var(--border); border-radius: 8px;
    overflow: hidden; background: var(--bg-2);
  }
  .rule-disabled { opacity: 0.5; }

  .rule-head {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 14px; border-bottom: 1px solid var(--border);
    background: var(--bg-3);
  }
  .rule-label-input {
    flex: 1; background: transparent; border: none; font-size: 13px;
    font-weight: 600; color: var(--text); font-family: inherit;
  }
  .rule-label-input:focus { outline: none; }

  .mode-badge {
    font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: 10px;
    background: rgba(100,100,100,.12); color: var(--text-5);
    text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap;
  }
  .mode-search { background: rgba(124,108,244,.12); color: var(--accent); }

  /* Toggle */
  .toggle { position: relative; display: inline-flex; cursor: pointer; }
  .toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
  .toggle-track {
    width: 32px; height: 18px; background: var(--border); border-radius: 9px;
    transition: background 0.15s;
  }
  .toggle-track::after {
    content: ''; position: absolute; top: 3px; left: 3px;
    width: 12px; height: 12px; background: #fff; border-radius: 50%;
    transition: transform 0.15s;
  }
  .toggle input:checked + .toggle-track { background: var(--accent); }
  .toggle input:checked + .toggle-track::after { transform: translateX(14px); }

  .rule-delete-btn {
    background: none; border: none; color: var(--text-5); cursor: pointer;
    font-size: 13px; padding: 2px 4px; line-height: 1;
  }
  .rule-delete-btn:hover { color: #f87171; }

  /* Body — two sections side by side on wider screens */
  .rule-body {
    display: flex; flex-direction: row; align-items: flex-start; gap: 0;
    border-bottom: 1px solid var(--border);
  }

  .section {
    flex: 1; padding: 12px 14px; display: flex; flex-direction: column; gap: 8px;
    min-width: 0;
  }
  .section + .section { border-left: 1px solid var(--border); }

  .section-head {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
    margin-bottom: 2px;
  }
  .section-title {
    font-size: 11px; font-weight: 700; color: var(--text-4);
    text-transform: uppercase; letter-spacing: 0.05em;
  }
  .section-hint { font-size: 11px; color: var(--text-5); margin-left: auto; }

  .logic-badge {
    border-radius: 3px; padding: 1px 5px; font-size: 10px; font-weight: 700;
  }
  .and-badge {
    background: rgba(124,108,244,.12); color: var(--accent);
  }
  .or-badge {
    background: rgba(34,197,94,.12); color: #22c55e;
  }

  .section-divider {
    display: flex; align-items: center; justify-content: center;
    padding: 0 6px; font-size: 16px; color: var(--text-5); flex-shrink: 0;
    border-left: 1px solid var(--border); border-right: 1px solid var(--border);
    background: var(--bg-3);
  }

  .targets-empty {
    font-size: 11px; color: var(--text-5); line-height: 1.5;
    border: 1px dashed var(--border); border-radius: 4px;
    padding: 6px 10px; margin: 0;
  }

  /* Criterion rows */
  .criterion-row { display: flex; align-items: center; gap: 6px; }

  .crit-select {
    background: var(--bg-3); border: 1px solid var(--border);
    border-radius: 4px; padding: 5px 8px; font-size: 12px;
    color: var(--text); font-family: inherit; cursor: pointer;
  }
  .crit-select:focus { outline: none; border-color: var(--accent); }
  .crit-op { min-width: 120px; }

  .crit-value {
    flex: 1; background: var(--bg-3); border: 1px solid var(--border);
    border-radius: 4px; padding: 5px 8px; font-size: 12px;
    color: var(--text); font-family: inherit; min-width: 0;
  }
  .crit-value:focus { outline: none; border-color: var(--accent); }

  .crit-remove {
    background: none; border: none; color: var(--text-5); cursor: pointer;
    font-size: 11px; padding: 2px 4px; flex-shrink: 0;
  }
  .crit-remove:hover { color: #f87171; }

  .add-criterion-btn {
    align-self: flex-start; background: none; border: 1px dashed var(--border);
    border-radius: 4px; color: var(--text-5); font-size: 11px;
    padding: 3px 10px; cursor: pointer;
  }
  .add-criterion-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* Boost slider */
  .boost-section { padding: 12px 14px; background: var(--bg-2); }
  .boost-danger { background: rgba(245,158,11,.04); }

  .boost-row { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
  .boost-label { font-size: 12px; color: var(--text-5); width: 38px; flex-shrink: 0; }

  .boost-slider { flex: 1; accent-color: var(--accent); cursor: pointer; height: 4px; }
  .slider-danger { accent-color: #f59e0b; }

  .boost-value {
    font-size: 12px; font-weight: 600; color: var(--accent);
    min-width: 52px; text-align: right; font-family: monospace;
  }
  .boost-value-danger { color: #f59e0b; }

  .boost-hint { font-size: 11px; color: var(--text-5); line-height: 1.5; }
  .boost-danger .boost-hint { color: #f59e0b; }

  /* Add rule button */
  .add-rule-btn {
    align-self: flex-start; background: none;
    border: 1px dashed var(--border); border-radius: 6px;
    color: var(--text-5); font-size: 13px; padding: 8px 16px; cursor: pointer;
  }
  .add-rule-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* ID picker */
  .id-picker-row { display: flex; align-items: center; gap: 6px; flex: 1; min-width: 0; }

  .id-chip {
    display: flex; align-items: center; gap: 6px;
    background: var(--bg-3); border: 1px solid var(--border);
    border-radius: 4px; padding: 3px 6px 3px 8px; min-width: 0; flex: 1;
  }
  .id-chip-inner { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; overflow: hidden; }
  .id-chip-title {
    font-size: 11px; font-weight: 500; color: var(--text);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .id-chip-id { font-size: 10px; color: var(--text-5); font-family: monospace; }
  .id-chip-clear {
    background: none; border: none; color: var(--text-5); cursor: pointer;
    font-size: 11px; padding: 1px 2px; flex-shrink: 0; line-height: 1;
  }
  .id-chip-clear:hover { color: #f87171; }

  .id-chip-empty { font-size: 12px; color: var(--text-5); flex: 1; }

  .browse-btn {
    flex-shrink: 0; background: var(--bg-3); border: 1px solid var(--border);
    border-radius: 4px; padding: 5px 10px; font-size: 12px; color: var(--text-4);
    cursor: pointer; white-space: nowrap;
  }
  .browse-btn:hover { border-color: var(--accent); color: var(--accent); }
</style>
