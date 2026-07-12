// @name Plans Transformer
// @description Transforms raw ratePlanOffer objects (from ratePlans.ratePlanOffers)
// into ISP-searchable plan documents. One doc per unique plan+line-count combination.
// @run_order 1

/**
 * Map a raw ratePlanOffer document to a clean ISP-searchable record.
 *
 * Input fields (ratePlanOffers items):
 *   displayName, shortDescription, sortOrder, billingSoc, serviceSoc,
 *   planFamilyClassification, minimumLineCount, maximumLineCount,
 *   totalBasePrice, totalContextPrice, discounts[], promotions[],
 *   planFeatures[{planfeature, disclaimer, isVisibleForProspectUser}],
 *   customerGroups[], isPROnlyPlan, isRDCPromosNotAllowed, benefitsAddOnSubCategory
 *
 * @param {Object} doc
 * @returns {Object}
 */
function transform(doc) {
  // ── Identity ──────────────────────────────────────────────────────────────
  // billingSoc is unique per plan+line-count combination (e.g. C25L1T1, TESSSL)
  var soc = doc.billingSoc || doc.serviceSoc || '';
  if (soc) doc.id = soc.toLowerCase();

  // ── Title ─────────────────────────────────────────────────────────────────
  var name    = doc.displayName || '';
  var lineMin = parseInt(doc.minimumLineCount, 10) || 0;
  var lineMax = parseInt(doc.maximumLineCount, 10) || 0;

  if (lineMin === lineMax && lineMin > 0) {
    doc.title = name || 'Plan';
  } else if (lineMin > 0 && lineMax > 0 && name) {
    doc.title = name + ' (' + lineMin + '–' + lineMax + ' lines)';
  } else {
    doc.title = name || 'Plan';
  }

  // ── Price ─────────────────────────────────────────────────────────────────
  var priceRaw = doc.totalContextPrice !== undefined ? doc.totalContextPrice : doc.totalBasePrice;
  doc.price = priceRaw !== undefined && priceRaw !== null
    ? (parseFloat(String(priceRaw).replace(/[^0-9.]/g, '')) || 0)
    : 0;

  if (doc.price && lineMin > 0) {
    doc.price_per_line = Math.round((doc.price / lineMin) * 100) / 100;
  }

  // ── Lines ─────────────────────────────────────────────────────────────────
  doc.min_lines = lineMin || 1;
  doc.max_lines = lineMax || lineMin || 1;

  // ── Customer group — flatten to a single lowercase tag ────────────────────
  var groups = Array.isArray(doc.customerGroups) ? doc.customerGroups : [];
  doc.customer_group = groups.length > 0 ? String(groups[0]).toLowerCase() : 'consumer';
  doc.customerGroups = groups;

  // ── Features — prefer rankedFeatures (injected by pre-processor from
  //   ratePlanFeatures.featuresByRank) over planFeatures when available ────────
  var ranked  = Array.isArray(doc.rankedFeatures) ? doc.rankedFeatures : [];
  var rawFeats = Array.isArray(doc.planFeatures)  ? doc.planFeatures  : [];
  var featLines = [];

  if (ranked.length > 0) {
    // rankedFeatures are already strings (shortDescription or "label: name")
    for (var i = 0; i < ranked.length; i++) {
      if (ranked[i]) featLines.push(String(ranked[i]));
    }
  } else {
    // Fallback: extract from planFeatures object array
    for (var i = 0; i < rawFeats.length; i++) {
      var f = rawFeats[i];
      if (!f) continue;
      var label = f.planfeature || f.label || f.name || f.featureName || '';
      if (label) featLines.push(String(label));
    }
  }

  doc.planFeatures    = featLines;
  doc.features_list   = featLines;
  delete doc.rankedFeatures; // already merged, no need to store separately

  // ── Promotions — replace array-of-objects with array-of-strings ───────────
  var rawPromos = Array.isArray(doc.promotions) ? doc.promotions : [];
  var promoNames = [];
  for (var j = 0; j < rawPromos.length; j++) {
    var p = rawPromos[j];
    if (!p) continue;
    var pname = p.promoDisplayName || p.name || '';
    var disc  = p.discountValue ? (' ' + (p.discountValue.display || '')) : '';
    if (pname) promoNames.push(String(pname) + disc);
  }
  doc.promotions      = promoNames;
  doc.promotions_list = promoNames;

  // ── Drop raw arrays that contain nested objects ───────────────────────────
  delete doc.benefitsAddOnSubCategory;

  // ── Description (rich searchable blob) ────────────────────────────────────
  var parts = [];
  if (doc.shortDescription) parts.push(doc.shortDescription);
  if (doc.price)            parts.push('$' + doc.price + '/mo' + (lineMin > 0 ? ' for ' + lineMin + ' line' + (lineMin !== 1 ? 's' : '') : ''));
  if (featLines.length)     parts.push(featLines.slice(0, 4).join('. '));
  if (promoNames.length)    parts.push(promoNames.join('. '));
  doc.description = parts.join(' \xb7 ');

  // ── Plan tier ─────────────────────────────────────────────────────────────
  var n = name.toLowerCase();
  if      (n.indexOf('beyond')     >= 0) doc.plan_tier = 'premium';
  else if (n.indexOf('more')       >= 0) doc.plan_tier = 'standard';
  else if (n.indexOf('essentials') >= 0 || n.indexOf('ess') >= 0) doc.plan_tier = 'essentials';
  else if (n.indexOf('value')      >= 0) doc.plan_tier = 'value';
  else                                   doc.plan_tier = 'other';

  return doc;
}
