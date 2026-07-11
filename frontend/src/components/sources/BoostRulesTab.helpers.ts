import type { BoostCriterion, BoostRule } from '../../lib/types';

export const OPERATORS = [
  { value: 'equals',   label: 'Equals' },
  { value: 'contains', label: 'Contains' },
  { value: 'regex',    label: 'Matches Regex' },
] as const;

export const DANGER_THRESHOLD = 0.5;

export function nanoid(): string {
  return Math.random().toString(36).slice(2, 10);
}

export function blankCriterion(forTrigger: boolean): BoostCriterion {
  return { field: forTrigger ? '__search_term__' : 'id', operator: 'equals', value: '' };
}

export function ruleMode(rule: BoostRule): 'search' | 'browse' {
  return rule.trigger.some(c => c.field === '__search_term__') ? 'search' : 'browse';
}

export function formatBoost(v: number): string {
  return `+${v.toFixed(2)}`;
}

export function boostZone(v: number): 'normal' | 'danger' {
  return v >= DANGER_THRESHOLD ? 'danger' : 'normal';
}
