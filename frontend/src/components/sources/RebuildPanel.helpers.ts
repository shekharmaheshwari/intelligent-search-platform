// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import type { IndexMeta, RestConnectorConfig, SitemapConnectorConfig, GSheetConnectorConfig } from '../../lib/types';

/**
 * Pre-populate form fields from the first saved REST connector on an index.
 * Returns an empty object when no REST connector is found.
 * External connectors (type ``'external'``) are deliberately excluded because
 * their ``url`` field is a product-page URL prefix, not a data API endpoint.
 */
export function getExistingRest(index: IndexMeta): Partial<RestConnectorConfig> {
  const c = index.connectors.find(c => c.type === 'rest');
  if (!c) return {};
  return {
    url:           String(c.url ?? ''),
    method:        String(c.method ?? 'GET'),
    headers:       (c.headers as Record<string, string>) ?? {},
    body:          String(c.body ?? ''),
    pre_processor: String(c.pre_processor ?? ''),
  };
}

/**
 * Pre-populate sitemap form fields from the first saved sitemap connector on an index.
 * Returns an empty object when no sitemap connector is found.
 */
export function getExistingSitemap(index: IndexMeta): Partial<SitemapConnectorConfig> {
  const c = index.connectors.find(c => c.type === 'sitemap');
  if (!c) return {};
  return {
    url:              String(c.url ?? ''),
    max_pages:        Number(c.max_pages ?? 50),
    content_selector: String(c.content_selector ?? ''),
    user_agent:       String(c.user_agent ?? ''),
    include_patterns: (c.include_patterns ?? []) as string[],
    exclude_patterns: (c.exclude_patterns ?? []) as string[],
    meta_fields:      (c.meta_fields ?? {}) as Record<string, string>,
  };
}

/**
 * Pre-populate Google Sheets form fields from the first saved gsheet connector on an index.
 * Returns an empty object when no gsheet connector is found.
 */
export function getExistingGsheet(index: IndexMeta): Partial<GSheetConnectorConfig> {
  const c = index.connectors.find(c => c.type === 'gsheet');
  if (!c) return {};
  return {
    spreadsheet_id: String(c.spreadsheet_id ?? ''),
    sheet_name:     String(c.sheet_name ?? 'Sheet1'),
    id_column:      String(c.id_column ?? ''),
    title_column:   String(c.title_column ?? ''),
    content_column: String(c.content_column ?? ''),
    url_column:     String(c.url_column ?? ''),
    field_map:      (c.field_map ?? {}) as Record<string, string>,
    skip_empty_rows: Boolean(c.skip_empty_rows ?? true),
    header_row:     Number(c.header_row ?? 1),
  };
}

/**
 * Parse a raw curl command string into individual form field values.
 *
 * Extracts HTTP method (``-X``), URL (first ``https?://`` token), request headers
 * (``-H`` flags), and request body (``-d`` / ``--data``).
 *
 * @returns An object with parsed fields, or an object with ``error`` set when
 *          the input does not start with ``curl`` or contains no URL.
 */
export function parseCurl(raw: string): {
  method: string; url: string; headersRaw: string; body: string; error?: string;
} {
  const s = raw.replace(/\\\n/g, ' ').replace(/\s+/g, ' ').trim();
  if (!s.startsWith('curl')) return { method: '', url: '', headersRaw: '{}', body: '', error: 'Must start with "curl"' };

  const mMatch = s.match(/-X\s+([A-Z]+)/);
  const method = mMatch ? mMatch[1] : 'GET';

  const urlMatch = s.match(/curl(?:\s+-[^\s]+)*\s+['"]?(https?:\/\/[^\s'"\\]+)['"]?/);
  if (!urlMatch) return { method, url: '', headersRaw: '{}', body: '', error: 'Could not find URL' };
  const url = urlMatch[1];

  const hdrs: Record<string, string> = {};
  // Match both -H and --header, with single or double quoted values
  const hRegex = /(?:--header|-H)\s+(?:'([^']*)'|"([^"]*)")/g;
  let hm: RegExpExecArray | null;
  while ((hm = hRegex.exec(s)) !== null) {
    const raw = hm[1] ?? hm[2];
    const colon = raw.indexOf(':');
    if (colon > 0) {
      hdrs[raw.slice(0, colon).trim()] = raw.slice(colon + 1).trim();
    }
  }

  const dMatch = s.match(/(?:--data|-d)\s+'([^']*)'/) ||
                 s.match(/(?:--data|-d)\s+"((?:[^"\\]|\\.)*)"/) ||
                 s.match(/(?:--data|-d)\s+(\{[^}]+\})/);
  const body = dMatch ? dMatch[1].replace(/\\"/g, '"') : '';

  return { method, url, headersRaw: JSON.stringify(hdrs, null, 2), body };
}

/**
 * Build a multi-line curl command preview from the current form values.
 * Automatically injects a ``Content-Type: application/json`` header when
 * a body is present and no Content-Type is already set.
 *
 * @returns A placeholder comment when ``u`` is empty.
 */
export function buildCurl(u: string, m: string, h: string, b: string): string {
  if (!u) return '# Enter a URL above to see the curl command';
  const lines: string[] = [`curl -X ${m} "${u}"`];
  try {
    const hdrs = JSON.parse(h || '{}');
    for (const [k, v] of Object.entries(hdrs)) {
      lines.push(`  -H "${k}: ${v}"`);
    }
  } catch { /* ignore */ }
  if (b.trim()) {
    if (!h.includes('Content-Type')) {
      lines.push(`  -H "Content-Type: application/json"`);
    }
    lines.push(`  -d '${b.trim()}'`);
  }
  return lines.join(' \\\n');
}
