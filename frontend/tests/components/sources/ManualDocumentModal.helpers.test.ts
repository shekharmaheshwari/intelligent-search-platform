// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import {
  inputKind, fieldLabel, splitList, joinList, toFormValues, toPayload, missingRequired, apiErrorMessage,
} from '../../../src/components/sources/ManualDocumentModal.helpers';
import type { ManualFormField } from '../../../src/lib/types';

const f = (name: string, type: string, required = false, group: ManualFormField['group'] = 'field'): ManualFormField =>
  ({ name, type, required, group });

const schema: ManualFormField[] = [
  f('title', 'string', true, 'core'),
  f('content', 'string', true, 'core'),
  f('url', 'string', false, 'core'),
  f('tags', 'string[]'),
  f('price', 'float'),
  f('stock', 'int32'),
  f('in_stock', 'bool'),
  f('line_prices', 'float[]'),
  f('location', 'geopoint'),
  f('popularity', 'int32', false, 'editorial'),
];

describe('inputKind', () => {
  it('maps Typesense types to inputs', () => {
    expect(inputKind(f('content', 'string'))).toBe('textarea');
    expect(inputKind(f('title', 'string'))).toBe('text');
    expect(inputKind(f('tags', 'string[]'))).toBe('list');
    expect(inputKind(f('n', 'int64'))).toBe('integer');
    expect(inputKind(f('p', 'float'))).toBe('number');
    expect(inputKind(f('b', 'bool'))).toBe('checkbox');
    expect(inputKind(f('l', 'float[]'))).toBe('number-list');
  });
  it('skips types the form cannot edit', () => {
    expect(inputKind(f('loc', 'geopoint'))).toBeNull();
    expect(inputKind(f('o', 'object'))).toBeNull();
  });
});

describe('fieldLabel / splitList', () => {
  it('humanises names', () => {
    expect(fieldLabel('boost_queries_equal')).toBe('Boost queries equal');
    expect(fieldLabel('chatContent')).toBe('Chat content');
  });
  it('splits one entry per line and keeps commas inside entries', () => {
    expect(splitList(' a\n\nb, c \r\nd')).toEqual(['a', 'b, c', 'd']);
  });
  it('joinList is the inverse of splitList', () => {
    const items = ['buy shoes, size 10', 'running'];
    expect(splitList(joinList(items))).toEqual(items);
  });
});

describe('toFormValues', () => {
  it('pre-fills stored values and returns unknown keys as custom fields', () => {
    const { values, custom } = toFormValues(schema, {
      title: 'T', content: 'C', tags: ['x', 'y'], price: 9.5, in_stock: true, campaign: 'spring',
    });
    expect(values.title).toBe('T');
    expect(values.tags).toBe('x\ny');
    expect(values.price).toBe('9.5');
    expect(values.in_stock).toBe(true);
    expect(values.stock).toBe('');
    expect(values).not.toHaveProperty('location');
    expect(custom).toEqual([{ name: 'campaign', value: 'spring', kind: 'text' }]);
  });
});

describe('toPayload', () => {
  it('types values from the schema and drops blanks', () => {
    const out = toPayload(schema, {
      title: ' T ', content: 'C', url: '', tags: 'a\nb', price: '9.5', stock: '3',
      in_stock: false, line_prices: '10\n20.5', popularity: '',
    }, [{ name: 'campaign', value: ' spring ', kind: 'text' }, { name: '', value: 'ignored', kind: 'text' }]);
    expect(out).toEqual({
      title: 'T', content: 'C', tags: ['a', 'b'], price: 9.5, stock: 3,
      in_stock: false, line_prices: [10, 20.5], campaign: 'spring',
    });
  });
  it('sends checkboxes as explicit booleans, unticked as false', () => {
    expect(toPayload(schema, { title: 'T', content: 'C', in_stock: true }).in_stock).toBe(true);
    expect(toPayload(schema, { title: 'T', content: 'C', in_stock: false }).in_stock).toBe(false);
    expect(toPayload(schema, { title: 'T', content: 'C' }).in_stock).toBe(false);
  });
  it('does not let a custom field overwrite a schema field', () => {
    expect(toPayload(schema, { title: 'T', content: 'C' }, [{ name: 'title', value: 'X', kind: 'text' }]).title).toBe('T');
  });
  it('round-trips stored values unchanged through an unrelated edit', () => {
    const stored = {
      title: 'T', content: 'C', tags: ['buy shoes, size 10', 'sale'], price: 9.5, in_stock: false,
      line_prices: [10, 20.5],
      phrases: ['red, blue', 'green'], priority: 3, active: true, sizes: [8, 9.5], note: 'a, b',
    };
    const { values, custom } = toFormValues(schema, stored);
    values.title = 'T2';
    expect(toPayload(schema, values, custom)).toEqual({ ...stored, title: 'T2' });
  });
  it('rejects non-numeric and fractional integers', () => {
    expect(() => toPayload(schema, { title: 'T', content: 'C', price: 'abc' })).toThrow('Price must be a number');
    expect(() => toPayload(schema, { title: 'T', content: 'C', stock: '1.5' })).toThrow('Stock must be a whole number');
  });
});

describe('missingRequired', () => {
  it('lists empty required fields', () => {
    expect(missingRequired(schema, { title: ' ', content: 'C' })).toEqual(['Title']);
    expect(missingRequired(schema, { title: 'T', content: 'C' })).toEqual([]);
  });
});

describe('apiErrorMessage', () => {
  it('extracts the FastAPI detail', () => {
    expect(apiErrorMessage(new Error('400 {"detail":"title is required"}'))).toBe('title is required');
  });
  it('falls back to the raw message', () => {
    expect(apiErrorMessage(new Error('500 Internal Server Error'))).toBe('500 Internal Server Error');
  });
});
