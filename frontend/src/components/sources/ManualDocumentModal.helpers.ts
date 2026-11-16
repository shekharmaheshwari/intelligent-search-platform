// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import type { ManualFormField, ManualDocFields } from '../../lib/types';

/** How a field is edited in the manual document form. */
export type InputKind = 'text' | 'textarea' | 'number' | 'integer' | 'checkbox' | 'list' | 'number-list';

/** Raw form state: text inputs hold strings, checkboxes hold booleans. */
export type FormValues = Record<string, string | boolean>;

/**
 * A field not in the index schema yet. ``kind`` is inferred from the stored
 * value when editing, so saving writes back the same type (a list stays a
 * list, a number stays a number). New custom fields are ``text``.
 */
export interface CustomField { name: string; value: string; kind: CustomKind }
export type CustomKind = 'text' | 'number' | 'bool' | 'list' | 'number-list';

/** True for kinds edited as one entry per line. */
export function isMultiline(kind: InputKind | CustomKind | null): boolean {
  return kind === 'list' || kind === 'number-list' || kind === 'textarea';
}

const INT_TYPES   = new Set(['int32', 'int64']);
const FLOAT_TYPES = new Set(['float']);

/** Pick the input for a Typesense type, or ``null`` for types the form cannot edit (objects, geopoints…). */
export function inputKind(field: Pick<ManualFormField, 'name' | 'type'>): InputKind | null {
  const t = field.type;
  if (field.name === 'content') return 'textarea';
  if (t === 'string' || t === 'auto' || t === 'string*') return 'text';
  if (t === 'string[]') return 'list';
  if (INT_TYPES.has(t)) return 'integer';
  if (FLOAT_TYPES.has(t)) return 'number';
  if (t === 'bool') return 'checkbox';
  if (t === 'int32[]' || t === 'int64[]' || t === 'float[]') return 'number-list';
  return null;
}

/** Human label for a field name: ``boost_queries_equal`` → ``Boost queries equal``. */
export function fieldLabel(name: string): string {
  const s = name.replace(/[_.]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').trim().toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * Split a list input into trimmed, non-empty entries — one entry per line, so
 * entries may themselves contain commas (``"buy shoes, size 10"``).
 */
export function splitList(raw: string): string[] {
  return raw.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
}

/** Inverse of ``splitList``: one entry per line. */
export function joinList(items: unknown[]): string {
  return items.map(String).join('\n');
}

function customKindOf(v: unknown): CustomKind {
  if (typeof v === 'number') return 'number';
  if (typeof v === 'boolean') return 'bool';
  if (Array.isArray(v)) return v.length > 0 && v.every(x => typeof x === 'number') ? 'number-list' : 'list';
  return 'text';
}

/**
 * Build the initial form state from the schema and (when editing) the stored values.
 * Stored keys the schema does not know about come back as custom fields.
 */
export function toFormValues(
  fields: ManualFormField[],
  stored: ManualDocFields = {},
): { values: FormValues; custom: CustomField[] } {
  const values: FormValues = {};
  const known = new Set<string>();
  for (const f of fields) {
    const kind = inputKind(f);
    if (!kind) continue;
    known.add(f.name);
    const v = stored[f.name];
    if (kind === 'checkbox') values[f.name] = v === true;
    else if (Array.isArray(v)) values[f.name] = joinList(v);
    else values[f.name] = v === undefined || v === null ? '' : String(v);
  }
  const custom: CustomField[] = Object.entries(stored)
    .filter(([k]) => !known.has(k))
    .map(([name, v]) => ({
      name,
      value: Array.isArray(v) ? joinList(v) : String(v),
      kind: customKindOf(v),
    }));
  return { values, custom };
}

/**
 * Convert form state into the request body, typing each value from its schema field.
 * Throws with a readable message when a number field holds non-numeric text.
 */
export function toPayload(
  fields: ManualFormField[],
  values: FormValues,
  custom: CustomField[] = [],
): ManualDocFields {
  const out: ManualDocFields = {};
  for (const f of fields) {
    const kind = inputKind(f);
    if (!kind) continue;
    const raw = values[f.name];
    if (kind === 'checkbox') {
      // Always send an explicit boolean: a missing field does not match
      // filters such as ``in_stock:=false``.
      out[f.name] = raw === true;
      continue;
    }
    const text = typeof raw === 'string' ? raw.trim() : '';
    if (!text) continue;
    if (kind === 'list') out[f.name] = splitList(text);
    else if (kind === 'number-list') out[f.name] = splitList(text).map(s => toNumber(f.name, s, false));
    else if (kind === 'integer') out[f.name] = toNumber(f.name, text, true);
    else if (kind === 'number') out[f.name] = toNumber(f.name, text, false);
    else out[f.name] = text;
  }
  for (const c of custom) {
    const name = c.name.trim();
    const value = c.value.trim();
    if (!name || !value || name in out) continue;
    switch (c.kind) {
      case 'number':      out[name] = toNumber(name, value, false); break;
      case 'bool':        out[name] = value.toLowerCase() === 'true'; break;
      case 'list':        out[name] = splitList(value); break;
      case 'number-list': out[name] = splitList(value).map(s => toNumber(name, s, false)); break;
      default:            out[name] = value;
    }
  }
  return out;
}

function toNumber(name: string, text: string, integer: boolean): number {
  const n = Number(text);
  if (!Number.isFinite(n) || (integer && !Number.isInteger(n))) {
    throw new Error(`${fieldLabel(name)} must be ${integer ? 'a whole number' : 'a number'}`);
  }
  return n;
}

/** Missing required fields, as labels, for the inline validation message. */
export function missingRequired(fields: ManualFormField[], values: FormValues): string[] {
  return fields
    .filter(f => f.required && !(typeof values[f.name] === 'string' && (values[f.name] as string).trim()))
    .map(f => fieldLabel(f.name));
}

/** Turn an ``api`` error (``"400 {\"detail\": \"…\"}"``) into the backend's message. */
export function apiErrorMessage(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  const brace = msg.indexOf('{');
  if (brace >= 0) {
    try {
      const detail = JSON.parse(msg.slice(brace)).detail;
      if (typeof detail === 'string') return detail;
    } catch { /* not JSON — fall through */ }
  }
  return msg;
}
