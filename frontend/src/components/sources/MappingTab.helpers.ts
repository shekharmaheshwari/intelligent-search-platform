// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

export interface FieldRow  { key: string; value: string }
export interface ValueRow  { field: string; from: string; to: string }

export function initFieldRows(connector: Record<string, unknown>): FieldRow[] {
  const fieldMap = (connector.field_map ?? {}) as Record<string, string>;
  return Object.entries(fieldMap).map(([k, v]) => ({ key: k, value: v }));
}

export function initValueRows(connector: Record<string, unknown>): ValueRow[] {
  const valueMap = (connector.value_map ?? {}) as Record<string, Record<string, string>>;
  return Object.entries(valueMap).flatMap(([field, inner]) =>
    Object.entries(inner).map(([from, to]) => ({ field, from, to }))
  );
}

export function initStaticFields(connector: Record<string, unknown>): FieldRow[] {
  const staticMap = (connector.static_fields ?? {}) as Record<string, string>;
  return Object.entries(staticMap).map(([k, v]) => ({ key: k, value: v }));
}

export function buildFieldMap(rows: FieldRow[]): Record<string, string> {
  return Object.fromEntries(
    rows.filter(r => r.key.trim()).map(r => [r.key.trim(), r.value.trim()])
  );
}

export function buildValueMap(rows: ValueRow[]): Record<string, Record<string, string>> {
  const result: Record<string, Record<string, string>> = {};
  for (const row of rows) {
    if (!row.field.trim() || !row.from.trim()) continue;
    const f = row.field.trim();
    if (!result[f]) result[f] = {};
    result[f][row.from.trim()] = row.to.trim();
  }
  return result;
}

export function buildStaticFields(rows: FieldRow[]): Record<string, string> {
  return Object.fromEntries(
    rows.filter(r => r.key.trim()).map(r => [r.key.trim(), r.value.trim()])
  );
}
