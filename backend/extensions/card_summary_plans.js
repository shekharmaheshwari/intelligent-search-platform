// @name Card Summary — Plans
// @description Builds cardSummary for service plans: a short, display-safe feature blurb
//   for direct customer-facing rendering (e.g. cards-only chat mode with no LLM call) —
//   deliberately excludes what a card already shows on its own (title, starting price).
//   Assign to the Plans index. Runs after plans_transformer (run_order 1).
// @type transform
// @assign none
// @run_order 2

/**
 * Add a ``cardSummary`` field: plain text, meant to be shown as-is next to
 * a plan card — not fed to an LLM (that's chatContent's job). Only covers
 * what isn't already a separate card field:
 *   - per-line price breakdown (a flat "price" field can't carry this —
 *     see plans_pre_processor.js)
 *   - data / international / hotspot signals, derived from features_list
 *   - current promotions
 *
 * Input: transformed plan document (output of plans_transformer.js).
 *   Key fields: eligible_lines, line_prices, price_per_line, min_lines,
 *               features_list, promotions_list, cheapest_for_lines,
 *               premium_for_lines, highlight_features.
 */
function transform(doc) {
  var parts = [];

  // ── Cheapest / most-premium badges (set by plans_pre_processor) ─────────
  // Ranked within the plan's own customer group, so non-consumer plans say
  // which group they were compared against ("Cheapest Student plan ...").
  var groups = Array.isArray(doc.customerGroups) ? doc.customerGroups : [];
  var group  = groups.length ? String(groups[0]) : '';
  var groupLabel = /^consumer$/i.test(group) || !group ? '' :
    group.toLowerCase().split('_').map(function(w) {
      return w.charAt(0).toUpperCase() + w.slice(1);
    }).join(' ') + ' ';
  // [1,2,3,5] -> "1–3 lines, 5 lines"; [1] -> "a single line"
  function lineLabels(arr) {
    var runs = [];
    arr.forEach(function(n) {
      var last = runs[runs.length - 1];
      if (last && n === last[1] + 1) last[1] = n; else runs.push([n, n]);
    });
    return runs.map(function(r) {
      if (r[0] === r[1]) return r[0] === 1 ? 'a single line' : r[0] + ' lines';
      return r[0] + '–' + r[1] + ' lines';
    }).join(', ');
  }
  var cheapest = Array.isArray(doc.cheapest_for_lines) ? doc.cheapest_for_lines : [];
  if (cheapest.length) parts.push('Cheapest ' + groupLabel + 'plan for ' + lineLabels(cheapest) + '.');
  var premium = Array.isArray(doc.premium_for_lines) ? doc.premium_for_lines : [];
  if (premium.length) parts.push('Most premium ' + groupLabel + 'plan for ' + lineLabels(premium) + '.');

  // ── Price per exact line count ───────────────────────────────────────────
  var lines  = Array.isArray(doc.eligible_lines) ? doc.eligible_lines : [];
  var prices = Array.isArray(doc.line_prices)    ? doc.line_prices    : [];
  if (lines.length > 1 && lines.length === prices.length) {
    var perLine = [];
    for (var k = 0; k < lines.length; k++) {
      perLine.push(lines[k] + ' line' + (lines[k] !== 1 ? 's' : '') + ': $' + prices[k] + '/mo');
    }
    parts.push('By line count — ' + perLine.join(', ') + '.');
  } else if (doc.price_per_line) {
    parts.push('$' + doc.price_per_line + '/mo per line (at ' + (doc.min_lines || 1) + ' line' + ((doc.min_lines || 1) !== 1 ? 's' : '') + ').');
  }

  // ── Features ──────────────────────────────────────────────────────────────
  // Prefer the pre-processor's highlight_features (only perks the plan
  // actually includes); fall back to keyword signals from features_list.
  // Card shows the top 6; reasoningData keeps the full list for chat.
  var highlights = (Array.isArray(doc.highlight_features) ? doc.highlight_features.filter(Boolean) : [])
    .slice(0, 6).map(function(h) { return String(h).trim(); });
  var feats = Array.isArray(doc.features_list) ? doc.features_list.filter(Boolean) : [];
  if (highlights.length) {
    parts.push(('Highlights: ' + highlights.join('; ')).replace(/\.?$/, '.'));
  } else if (feats.length) {
    var featText = feats.join(' ').toLowerCase();

    if (featText.indexOf('unlimited') >= 0) {
      parts.push('Unlimited data.');
    } else {
      var dataMatch = featText.match(/(\d+\s*gb)/i);
      if (dataMatch) parts.push(dataMatch[1].toUpperCase() + ' data.');
    }

    var intlKeywords = ['international', 'europe', 'global', 'travel', 'roaming', 'mexico', 'canada'];
    for (var i = 0; i < intlKeywords.length; i++) {
      if (featText.indexOf(intlKeywords[i]) >= 0) { parts.push('International/travel data included.'); break; }
    }

    if (featText.indexOf('hotspot') >= 0 || featText.indexOf('tethering') >= 0) {
      parts.push('Mobile hotspot included.');
    }
  }

  // ── Promotions ────────────────────────────────────────────────────────────
  var promos = Array.isArray(doc.promotions_list) ? doc.promotions_list.filter(Boolean) : [];
  if (promos.length) parts.push(promos.slice(0, 3).join('. ') + '.');

  doc.reasoningData = buildReasoningData(doc);
  doc.cardSummary = parts.join(' ');
  return doc;
}

// Structured facts for ALL reasoning tasks. Mappings belong to this extension,
// not the Python chat service. Only explicit source values are copied; no defaults.
function buildReasoningData(doc) {
  var fields = [["eligible_lines", "Eligible line counts"], ["line_prices", "Prices by eligible line count", "USD/month"], ["price_per_line", "Price per line", "USD/month"], ["min_lines", "Minimum lines"], ["features_list", "Included features"], ["promotions_list", "Promotions"], ["cheapest_for_lines", "Cheapest plan at line counts"], ["premium_for_lines", "Most premium plan at line counts"], ["highlight_features", "Highlights"]];
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
