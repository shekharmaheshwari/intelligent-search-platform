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
 *               features_list, promotions_list.
 */
function transform(doc) {
  var parts = [];

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

  // ── Features (data / international / hotspot) ────────────────────────────
  var feats = Array.isArray(doc.features_list) ? doc.features_list.filter(Boolean) : [];
  if (feats.length) {
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

  doc.cardSummary = parts.join(' ');
  return doc;
}
