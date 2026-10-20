// @name Card Summary — Phones
// @description Builds cardSummary for phones/tablets: a short, display-safe feature/spec
//   blurb for direct customer-facing rendering (e.g. cards-only chat mode with no LLM
//   call) — deliberately excludes what a card already shows on its own (title, price,
//   manufacturer, color swatches, in-stock badge). Assign to the Phones (and Tablets)
//   index. Runs after catalog_mapper (run_order 1) and version_score (run_order 1).
// @type transform
// @assign none
// @run_order 3

/**
 * Add a ``cardSummary`` field: plain text, HTML-stripped, meant to be shown
 * as-is next to a product card — not fed to an LLM (that's chatContent's
 * job). Only covers what isn't already a separate card field:
 *   - operating system / form factor
 *   - financing
 *   - promotions
 *   - a cleaned feature/spec snippet from long_description
 *
 * Input: mapped catalog document (output of catalog_mapper.js).
 *   Key fields: operating_system, form_factor, monthly_payment, badges, long_description.
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

  // ── Operating system ──────────────────────────────────────────────────────
  if (doc.operating_system) parts.push(doc.operating_system + '.');

  // ── Form factor ───────────────────────────────────────────────────────────
  var form = doc.form_factor || '';
  if (form === 'fold') parts.push('Foldable phone.');
  else if (form === 'flip') parts.push('Flip phone.');

  // ── Financing ─────────────────────────────────────────────────────────────
  if (doc.monthly_payment) parts.push('Or $' + doc.monthly_payment + '/mo with financing.');

  // ── Promotions / badges ───────────────────────────────────────────────────
  var badges = Array.isArray(doc.badges) ? doc.badges.filter(Boolean) : [];
  if (badges.length) parts.push(badges.join(', ') + '.');

  // ── Key features (HTML-stripped, capped) ─────────────────────────────────
  var longDesc = stripHtml(doc.long_description || '');
  if (longDesc) {
    var snippet = longDesc.length > 220 ? longDesc.slice(0, 220).replace(/\s+\S*$/, '') + '…' : longDesc;
    parts.push(snippet);
  }

  doc.reasoningData = buildReasoningData(doc);
  doc.cardSummary = parts.join(' ');
  return doc;
}

// Structured facts for ALL reasoning tasks. Mappings belong to this extension,
// not the Python chat service. Only explicit source values are copied; no defaults.
function buildReasoningData(doc) {
  var fields = [["operating_system", "Operating system"], ["form_factor", "Form factor"], ["processor", "Processor"], ["ram", "RAM"], ["storage", "Storage"], ["battery_life_hours", "Battery life", "hours"], ["camera", "Camera"], ["screen_size_inches", "Screen size", "inches"], ["weight_grams", "Weight", "g"], ["water_resistance_rating", "Water resistance"]];
  var facts = [];
  function scalar(v) {
    return (typeof v === 'string' && v.trim().length > 0 && v.length <= 2000)
      || typeof v === 'boolean' || (typeof v === 'number' && isFinite(v));
  }
  fields.forEach(function (field) {
    var value = doc[field[0]];
    if (!scalar(value) && !(Array.isArray(value) && value.length > 0
        && value.length <= 20 && value.every(scalar))) return;
    var fact = {key: field[0], label: field[1], value: value, source: field[0]};
    if (field[2]) fact.unit = field[2];
    facts.push(fact);
  });
  return {facts: facts};
}
