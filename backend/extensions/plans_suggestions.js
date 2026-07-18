// @name Plans Suggestions
// @description Generates typeahead search suggestions from rate plan documents (plans3 index).
// Guards on plan_tier so this extension is harmless when assigned to non-plan indexes.
// @assign all
// @run_order 2

/**
 * Pass-through transform — plans documents are already shaped by plans_transformer.
 * @param {Object} doc
 * @returns {Object}
 */
function transform(doc) {
  return doc;
}

/**
 * Extract typeahead suggestions from a rate plan document.
 *
 * Generates suggestions from:
 *   - Plan name (title)
 *   - Tier-based phrases (e.g. "premium plan", "essentials plan")
 *   - Line-count phrases (e.g. "2 line plan", "family plan", "single line plan")
 *   - Feature-driven phrases (e.g. "plan with Netflix", "plan with hotspot")
 *   - Price-range phrases (e.g. "plan under $50")
 *   - Promotion phrases (e.g. "plan with free phone")
 *
 * Returns [] for non-plan documents (no plan_tier field) so the extension
 * is safe when auto-assigned to device, plant, or account indexes.
 *
 * @param {Object} doc - ISP document from Typesense.
 * @returns {Array<{query: string, category: string|undefined}>}
 */
function extractSuggestions(doc) {
  // Guard: only run on plan documents
  if (!doc.plan_tier) return [];

  var results = [];
  var seen = {};

  function add(query, category) {
    var q = String(query || '').trim().toLowerCase();
    if (!q || q.length < 3 || seen[q]) return;
    seen[q] = true;
    var entry = { query: q };
    if (category) entry.category = category;
    results.push(entry);
  }

  var cat = doc.category || 'consumerPlans';

  // Plan name as-is
  if (doc.title) add(doc.title, cat);

  // Tier-based phrases
  var tier = doc.plan_tier || '';
  if (tier && tier !== 'other') {
    add(tier + ' plan', cat);
    add(tier + ' unlimited plan', cat);
  }

  // Line-count phrases
  var minLines = parseInt(doc.min_lines, 10) || 0;
  var maxLines = parseInt(doc.max_lines, 10) || 0;
  if (minLines === 1 && maxLines === 1) {
    add('single line plan', cat);
    add('1 line plan', cat);
  } else if (minLines === maxLines && minLines > 0) {
    add(minLines + ' line plan', cat);
    add(minLines + ' lines', cat);
  } else if (minLines > 0) {
    add(minLines + ' line plan', cat);
    if (minLines >= 3) add('family plan', cat);
  }

  // Price-range phrases
  var price = parseFloat(doc.price) || 0;
  if (price > 0) {
    var bucket = Math.ceil(price / 10) * 10;
    add('plan under $' + bucket, cat);
    add('$' + Math.floor(price) + ' plan', cat);
  }

  // Feature-driven phrases — top 3 features only to avoid noise
  var features = Array.isArray(doc.features_list) ? doc.features_list : [];
  var featureKeywords = [
    'netflix', 'hulu', 'disney', 'hotspot', 'international', 'roaming',
    'mexico', 'canada', '5g', 'data', 'streaming', 'music', 'apple tv',
    'phones on us', 'free phone', 'tidal', 'paramount'
  ];
  for (var i = 0; i < Math.min(features.length, 5); i++) {
    var feat = String(features[i] || '').toLowerCase();
    for (var k = 0; k < featureKeywords.length; k++) {
      var kw = featureKeywords[k];
      if (feat.indexOf(kw) >= 0) {
        add('plan with ' + kw, cat);
        break;
      }
    }
  }

  // Promotion phrases
  var promos = Array.isArray(doc.promotions_list) ? doc.promotions_list : [];
  for (var j = 0; j < Math.min(promos.length, 3); j++) {
    var promo = String(promos[j] || '').trim().toLowerCase();
    if (promo && promo.length >= 4) {
      add(promo, cat);
    }
  }

  // Generic always-present suggestions
  add('unlimited plan', cat);
  add('best plan', cat);
  add('cheapest plan', cat);

  return results;
}
