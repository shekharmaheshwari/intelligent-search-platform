// @name Chat Content — Plans
// @description Builds chatContent for service plans: price per line, features, data, international coverage, promotions.
//   Assign to the Plans index. Runs after plans_transformer (run_order 1).
// @type transform
// @assign none
// @run_order 2

/**
 * Add a ``chatContent`` field optimised for the chat RAG pipeline.
 *
 * Covers the query types from the chat improvement plan:
 *   - "cheapest plan for 2 lines"                    → price + line count
 *   - "unlimited premium plan — free phone?"         → plan_tier + promotions
 *   - "going to europe, which plan covers data?"     → international features
 *   - "is 10GB enough for streaming music daily?"    → data amount + usage advisory
 *   - "what's the price per line?"                   → price_per_line
 *
 * Input: transformed plan document (output of plans_transformer.js).
 *   Key fields: title, plan_tier, price, min_lines, max_lines, price_per_line,
 *               customer_group, shortDescription, features_list, promotions_list.
 */
function transform(doc) {
  var parts = [];

  var title = doc.title || doc.displayName || '';
  var tier  = doc.plan_tier || '';

  // ── Identity & tier ───────────────────────────────────────────────────────
  if (title) parts.push(title + (tier ? ' (' + tier + ' tier)' : '') + '.');

  // ── Short description ─────────────────────────────────────────────────────
  if (doc.shortDescription) parts.push(doc.shortDescription + '.');

  // ── Price & lines ─────────────────────────────────────────────────────────
  var price    = doc.price;
  var minLines = doc.min_lines || 1;
  var maxLines = doc.max_lines || minLines;
  if (price != null && price > 0) {
    var lineText = minLines === maxLines
      ? minLines + ' line' + (minLines !== 1 ? 's' : '')
      : minLines + '–' + maxLines + ' lines';
    parts.push('Price: $' + price + '/mo for ' + lineText + '.');
  } else if (price === 0) {
    parts.push('Free plan.');
  }

  // ── Price per line ────────────────────────────────────────────────────────
  if (doc.price_per_line) {
    parts.push('Price per line: $' + doc.price_per_line + '/mo.');
  }

  // ── Customer segment ──────────────────────────────────────────────────────
  var group = doc.customer_group || '';
  if (group && group !== 'consumer') {
    parts.push('Available for: ' + group + ' customers.');
  }

  // ── Features ──────────────────────────────────────────────────────────────
  var feats = Array.isArray(doc.features_list) ? doc.features_list.filter(Boolean) : [];
  if (feats.length) {
    parts.push('Features: ' + feats.slice(0, 6).join('. ') + '.');

    var featText = feats.join(' ').toLowerCase();

    // Data amount
    var hasUnlimited = featText.indexOf('unlimited') >= 0;
    var dataMatch    = featText.match(/(\d+\s*gb)/i);
    if (hasUnlimited) {
      parts.push('Data: Unlimited.');
    } else if (dataMatch) {
      parts.push('Data: ' + dataMatch[1].toUpperCase() + '.');
    }

    // International / travel coverage
    var intlKeywords = ['international', 'europe', 'global', 'travel', 'roaming', 'mexico', 'canada'];
    var hasIntl = false;
    for (var i = 0; i < intlKeywords.length; i++) {
      if (featText.indexOf(intlKeywords[i]) >= 0) { hasIntl = true; break; }
    }
    if (hasIntl) parts.push('International / travel data coverage: Included.');

    // Hotspot
    if (featText.indexOf('hotspot') >= 0 || featText.indexOf('tethering') >= 0) {
      parts.push('Mobile hotspot: Included.');
    }
  }

  // ── Promotions ────────────────────────────────────────────────────────────
  var promos = Array.isArray(doc.promotions_list) ? doc.promotions_list.filter(Boolean) : [];
  if (promos.length) {
    parts.push('Current promotions: ' + promos.slice(0, 3).join('. ') + '.');
  }

  doc.chatContent = parts.join(' ');
  return doc;
}
