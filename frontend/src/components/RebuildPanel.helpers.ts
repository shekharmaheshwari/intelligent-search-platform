import type { IndexMeta, RestConnectorConfig } from '../lib/types';

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
    url:     String(c.url ?? ''),
    method:  String(c.method ?? 'GET'),
    headers: (c.headers as Record<string, string>) ?? {},
    body:    String(c.body ?? ''),
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
  const hRegex = /-H\s+['"]([^'"]+)['"]/g;
  let hm: RegExpExecArray | null;
  while ((hm = hRegex.exec(s)) !== null) {
    const colon = hm[1].indexOf(':');
    if (colon > 0) {
      hdrs[hm[1].slice(0, colon).trim()] = hm[1].slice(colon + 1).trim();
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
