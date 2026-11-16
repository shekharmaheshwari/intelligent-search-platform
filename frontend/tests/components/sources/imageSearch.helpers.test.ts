// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import {
  validateImageFile, scaledSize, imageFromTransfer, firstUrl, docImageUrl,
  matchPercent, describeImageRun, apiDetail, MAX_IMAGE_FILE_BYTES,
} from '../../../src/components/sources/imageSearch.helpers';
import type { DocumentItem } from '../../../src/lib/types';

const doc = (extra: Record<string, unknown>): DocumentItem =>
  ({ id: '1', title: 't', content: 'c', url: '', source_type: 'rest', indexed_at: 0, ...extra });

describe('validateImageFile', () => {
  it('accepts supported images under the limit', () => {
    expect(validateImageFile({ type: 'image/jpeg', size: 1000 })).toBeNull();
    expect(validateImageFile({ type: 'image/webp', size: MAX_IMAGE_FILE_BYTES })).toBeNull();
  });
  it('rejects other types and oversized files', () => {
    expect(validateImageFile({ type: 'image/tiff', size: 10 })).toMatch(/JPEG/);
    expect(validateImageFile({ type: 'application/pdf', size: 10 })).toMatch(/JPEG/);
    expect(validateImageFile({ type: 'image/png', size: MAX_IMAGE_FILE_BYTES + 1 })).toMatch(/20 MB/);
  });
});

describe('scaledSize', () => {
  it('fits the longest side to the max, keeping aspect ratio', () => {
    expect(scaledSize(4000, 3000)).toEqual({ width: 512, height: 384 });
    expect(scaledSize(600, 1200, 300)).toEqual({ width: 150, height: 300 });
  });
  it('never upscales', () => {
    expect(scaledSize(200, 100)).toEqual({ width: 200, height: 100 });
  });
});

describe('imageFromTransfer', () => {
  const file = (type: string) => new File(['x'], 'f', { type });
  it('finds an image among clipboard items', () => {
    const img = file('image/png');
    const data = {
      items: [
        { kind: 'string', type: 'text/plain', getAsFile: () => null },
        { kind: 'file', type: 'image/png', getAsFile: () => img },
      ],
      files: [],
    } as unknown as DataTransfer;
    expect(imageFromTransfer(data)).toBe(img);
  });
  it('falls back to dropped files and ignores non-images', () => {
    const img = file('image/jpeg');
    expect(imageFromTransfer({ items: [], files: [file('text/plain'), img] } as unknown as DataTransfer)).toBe(img);
    expect(imageFromTransfer({ items: [], files: [file('text/plain')] } as unknown as DataTransfer)).toBeNull();
    expect(imageFromTransfer(null)).toBeNull();
  });
});

describe('firstUrl / docImageUrl', () => {
  it('handles strings, lists and objects like the backend', () => {
    expect(firstUrl(' https://x.io/a.jpg ')).toBe('https://x.io/a.jpg');
    expect(firstUrl(['nope', { src: 'https://x.io/b.png' }])).toBe('https://x.io/b.png');
    expect(firstUrl({ url: 'ftp://x' })).toBeNull();
    expect(firstUrl(42)).toBeNull();
  });
  it('reads the field from the doc, else from metadata', () => {
    expect(docImageUrl(doc({ image: 'https://x.io/top.jpg' }), 'image')).toBe('https://x.io/top.jpg');
    const meta = JSON.stringify({ images: [{ src: 'https://x.io/m.jpg' }] });
    expect(docImageUrl(doc({ metadata: meta }), 'images')).toBe('https://x.io/m.jpg');
    expect(docImageUrl(doc({ metadata: '{bad json' }), 'images')).toBeNull();
    expect(docImageUrl(doc({}), '')).toBeNull();
  });
});

describe('matchPercent', () => {
  it('maps cosine distance to a clamped 0–100 score', () => {
    expect(matchPercent(0)).toBe(100);
    expect(matchPercent(0.078)).toBe(92);
    expect(matchPercent(1.4)).toBe(0);
    expect(matchPercent(-0.01)).toBe(100);
  });
});

describe('describeImageRun', () => {
  const now = 10_000;
  it('covers not-run, running, done and error', () => {
    expect(describeImageRun(null)).toMatch(/not been indexed/);
    expect(describeImageRun({ state: 'running' })).toMatch(/Indexing/);
    expect(describeImageRun({ state: 'done', docs: 8, indexed: 7, embedded: 2, failed: 1, finished_at: now - 120 }, now))
      .toBe('7 of 8 products have image vectors · 2 newly embedded · 1 images could not be loaded · 2 min ago');
    expect(describeImageRun({ state: 'done', docs: 8, indexed: 8, embedded: 0, finished_at: now - 5 }, now))
      .toBe('8 of 8 products have image vectors · just now');
    expect(describeImageRun({ state: 'error', error: 'boom', finished_at: now - 7200 }, now))
      .toBe('Last run failed: boom · 2 h ago');
  });
});

describe('apiDetail', () => {
  it('extracts FastAPI detail or falls back', () => {
    expect(apiDetail(new Error('409 {"detail":"re-index images"}'))).toBe('re-index images');
    expect(apiDetail(new Error('500 oops'))).toBe('500 oops');
  });
});
