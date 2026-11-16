// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import type { DocumentItem, ImageIndexRun } from '../../lib/types';

/** Image types the backend accepts. */
export const ACCEPTED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/bmp'];
/** Largest file accepted before resizing (the upload itself is resized to ≤512px). */
export const MAX_IMAGE_FILE_BYTES = 20 * 1024 * 1024;
/** Longest side, in px, of the image sent to the backend. */
export const UPLOAD_MAX_SIDE = 512;

/** Reason a file cannot be used as a search image, or ``null`` when it is fine. */
export function validateImageFile(file: { type: string; size: number }): string | null {
  if (!ACCEPTED_IMAGE_TYPES.includes(file.type)) return 'Use a JPEG, PNG, WebP, GIF or BMP image';
  if (file.size > MAX_IMAGE_FILE_BYTES) return 'Image is larger than 20 MB';
  return null;
}

/** Size that fits ``w×h`` inside ``max×max`` keeping the aspect ratio (never upscales). */
export function scaledSize(w: number, h: number, max = UPLOAD_MAX_SIDE): { width: number; height: number } {
  const scale = Math.min(1, max / Math.max(w, h));
  return { width: Math.max(1, Math.round(w * scale)), height: Math.max(1, Math.round(h * scale)) };
}

/**
 * Downsize an image file in the browser and return it as a JPEG data URL.
 * Keeps uploads small (~30–60 KB) whatever the camera resolution.
 */
export async function fileToSearchDataUrl(file: Blob, max = UPLOAD_MAX_SIDE): Promise<string> {
  const bitmap = await createImageBitmap(file);
  try {
    const { width, height } = scaledSize(bitmap.width, bitmap.height, max);
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas is not available');
    ctx.fillStyle = '#fff';            // transparent PNGs → white, not black, in JPEG
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(bitmap, 0, 0, width, height);
    return canvas.toDataURL('image/jpeg', 0.85);
  } finally {
    bitmap.close();
  }
}

/** The first image file in a paste or drop, if any. */
export function imageFromTransfer(data: DataTransfer | null | undefined): File | null {
  if (!data) return null;
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const f = item.getAsFile();
      if (f) return f;
    }
  }
  for (const f of Array.from(data.files ?? [])) {
    if (f.type.startsWith('image/')) return f;
  }
  return null;
}

/** First http(s) URL in a value shaped like a string, list, or ``{url|src|href}`` object. */
export function firstUrl(value: unknown): string | null {
  if (typeof value === 'string') {
    const v = value.trim();
    return /^https?:\/\//.test(v) ? v : null;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const u = firstUrl(item);
      if (u) return u;
    }
    return null;
  }
  if (value && typeof value === 'object') {
    const o = value as Record<string, unknown>;
    return firstUrl(o.url ?? o.src ?? o.href);
  }
  return null;
}

/** Product image URL of a result document (top-level field, else inside ``metadata``). */
export function docImageUrl(doc: DocumentItem, field: string): string | null {
  if (!field) return null;
  let value: unknown = doc[field];
  if (value === undefined && typeof doc.metadata === 'string') {
    try { value = (JSON.parse(doc.metadata) as Record<string, unknown>)[field]; } catch { value = undefined; }
  }
  return firstUrl(value);
}

/** Cosine distance (0 = identical) → a 0–100 match score for display. */
export function matchPercent(distance: number): number {
  return Math.round(Math.max(0, Math.min(1, 1 - distance)) * 100);
}

/** One-line summary of an image indexing run for the settings card. */
export function describeImageRun(run: ImageIndexRun | null, nowSec = Date.now() / 1000): string {
  if (!run) return 'Images have not been indexed yet.';
  if (run.state === 'running') return 'Indexing product images…';
  const when = run.finished_at ? ` · ${relative(nowSec - run.finished_at)}` : '';
  if (run.state === 'error') return `Last run failed: ${run.error ?? 'unknown error'}${when}`;
  const parts = [`${run.indexed ?? 0} of ${run.docs ?? 0} products have image vectors`];
  if (run.embedded) parts.push(`${run.embedded} newly embedded`);
  if (run.failed) parts.push(`${run.failed} images could not be loaded`);
  return parts.join(' · ') + when;
}

function relative(sec: number): string {
  if (sec < 60) return 'just now';
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ago`;
  return `${Math.floor(sec / 86400)} d ago`;
}

/** Turn an ``api`` error (``"400 {\"detail\": \"…\"}"``) into the backend's message. */
export function apiDetail(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  const brace = msg.indexOf('{');
  if (brace >= 0) {
    try {
      const detail = JSON.parse(msg.slice(brace)).detail;
      if (typeof detail === 'string') return detail;
    } catch { /* not JSON */ }
  }
  return msg;
}
