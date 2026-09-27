// @name Card Summary — Accessories
// @description Builds cardSummary for accessories: a short, display-safe feature/spec
//   blurb for direct customer-facing rendering (e.g. cards-only chat mode with no LLM
//   call) — deliberately excludes what a card already shows on its own (title, price,
//   manufacturer, category, color swatches, in-stock badge). Assign to the Accessories
//   index. Runs after catalog_mapper (run_order 1).
// @type transform
// @assign none
// @run_order 3

/**
 * Add a ``cardSummary`` field: plain text, HTML-stripped, meant to be shown
 * as-is next to a product card — not fed to an LLM (that's chatContent's
 * job). Only covers what isn't already a separate card field:
 *   - device compatibility / size
 *   - promotions
 *   - a cleaned feature/spec snippet from long_description
 *
 * Input: mapped catalog document (output of catalog_mapper.js, type='accessory').
 *   Key fields: display, compatible_devices, device_universal, badges, long_description.
 */
var HTML_ENTITIES = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
  middot: '·', hellip: '…', mdash: '—', ndash: '–',
  rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“',
  reg: '®', trade: '™', copy: '©', deg: '°',
};

function decodeEntities(text) {
  return text.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, function (m, code) {
    if (code.charAt(0) === '#') {
      var cp = code.charAt(1).toLowerCase() === 'x'
        ? parseInt(code.slice(2), 16)
        : parseInt(code.slice(1), 10);
      return isNaN(cp) ? m : String.fromCharCode(cp);
    }
    return HTML_ENTITIES.hasOwnProperty(code) ? HTML_ENTITIES[code] : m;
  });
}

function stripHtml(html) {
  var noTags = String(html).replace(/<[^>]*>/g, ' ');
  return decodeEntities(noTags).replace(/\s+/g, ' ').trim();
}

function transform(doc) {
  var parts = [];
  var display = doc.display || doc.title || '';

  // ── Device compatibility ──────────────────────────────────────────────────
  if (Array.isArray(doc.compatible_devices) && doc.compatible_devices.length) {
    parts.push('Compatible with ' + doc.compatible_devices.join(', ') + '.');
  } else if (doc.device_universal === true) {
    parts.push('Fits any phone or device.');
  }

  // ── Watch band / size (from display name) ────────────────────────────────
  var sizeMatch = display.match(/(\d+\s*mm)/i);
  if (sizeMatch) parts.push('Size: ' + sizeMatch[1] + '.');

  // ── Promotions / badges ───────────────────────────────────────────────────
  var badges = Array.isArray(doc.badges) ? doc.badges.filter(Boolean) : [];
  if (badges.length) parts.push(badges.join(', ') + '.');

  // ── Key features (HTML-stripped, capped) ─────────────────────────────────
  var longDesc = stripHtml(doc.long_description || '');
  if (longDesc) {
    var snippet = longDesc.length > 220 ? longDesc.slice(0, 220).replace(/\s+\S*$/, '') + '…' : longDesc;
    parts.push(snippet);
  }

  doc.cardSummary = parts.join(' ');
  return doc;
}
