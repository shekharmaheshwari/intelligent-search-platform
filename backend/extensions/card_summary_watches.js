// @name Card Summary — Watches
// @description Builds cardSummary for smartwatches: a short, display-safe feature/spec
//   blurb for direct customer-facing rendering (e.g. cards-only chat mode with no LLM
//   call) — deliberately excludes what a card already shows on its own (title, price,
//   manufacturer, color swatches, in-stock badge). Assign to the Smartwatches index.
//   Runs after catalog_mapper (run_order 1).
// @type transform
// @assign none
// @run_order 3

/**
 * Add a ``cardSummary`` field: plain text, HTML-stripped, meant to be shown
 * as-is next to a product card — not fed to an LLM (that's chatContent's
 * job). Only covers what isn't already a separate card field:
 *   - GPS / cellular, OS compatibility, case size
 *   - fitness tracking
 *   - promotions
 *   - a cleaned feature/spec snippet from long_description
 *
 * GPS and cellular are inferred from the product display name and
 * description, same as chatContent — the raw catalog API doesn't expose
 * these as discrete fields.
 *
 * Input: mapped catalog document (output of catalog_mapper.js).
 *   Key fields: display, description, operating_system, badges, long_description.
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
  var display  = doc.display || doc.title || '';
  var combined = (display + ' ' + (doc.description || '')).toLowerCase();

  // ── GPS / Cellular ────────────────────────────────────────────────────────
  var hasGPS = combined.indexOf('gps') >= 0;
  if (hasGPS) parts.push('GPS.');
  var hasCellular = combined.indexOf('cellular') >= 0
    || combined.indexOf('+ lte') >= 0
    || combined.indexOf('lte') >= 0
    || combined.indexOf('4g') >= 0;
  parts.push(hasCellular
    ? 'Cellular/LTE — works independently without a nearby phone.'
    : 'Requires a nearby phone for calls and data.');

  // ── OS / device compatibility ─────────────────────────────────────────────
  var os = (doc.operating_system || '').toLowerCase();
  if (os.indexOf('watchos') >= 0 || os.indexOf('apple') >= 0 || combined.indexOf('iphone') >= 0) {
    parts.push('Pairs with iPhone.');
  } else if (os.indexOf('wear') >= 0 || os.indexOf('android') >= 0) {
    parts.push('Pairs with Android phones.');
  }

  // ── Case size (from display name) ─────────────────────────────────────────
  var sizeMatch = display.match(/(\d+\s*mm)/i);
  if (sizeMatch) parts.push('Case size: ' + sizeMatch[1] + '.');

  // ── Fitness features ─────────────────────────────────────────────────────
  var fitnessKeywords = ['heart rate', 'fitness', 'workout', 'running', 'health', 'ecg', 'blood oxygen', 'sleep', 'exercise'];
  for (var i = 0; i < fitnessKeywords.length; i++) {
    if (combined.indexOf(fitnessKeywords[i]) >= 0) { parts.push('Health and fitness tracking.'); break; }
  }

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
