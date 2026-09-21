// @name Plans Transformer
// @description Transforms raw ratePlanOffer objects (enriched by Plans Pre-Processor
// with a price_by_lines table) into ISP-searchable plan documents. One doc per plan.
// @run_order 1

/**
 * Map a raw ratePlanOffer document to a clean ISP-searchable record.
 *
 * Input fields (ratePlanOffers items, enriched by Plans Pre-Processor):
 *   displayName, shortDescription, sortOrder, billingSoc, serviceSoc,
 *   planFamilyClassification, minimumLineCount, maximumLineCount,
 *   totalBasePrice, totalContextPrice, discounts[], promotions[],
 *   planFeatures[{planfeature, disclaimer, isVisibleForProspectUser}],
 *   customerGroups[], isPROnlyPlan, isRDCPromosNotAllowed, benefitsAddOnSubCategory,
 *   price_by_lines {"<lineCount>": <price>, ...} — every real price this plan
 *   has across the exact line counts it's offered at (added by the pre-processor;
 *   see that file for why this can't just be a single `price` field on the raw
 *   offer — the same billingSoc reprices per exact line count).
 *
 * @param {Object} doc
 * @returns {Object}
 */
function transform(doc) {
  // ── Identity ──────────────────────────────────────────────────────────────
  // billingSoc is unique per plan (one doc per plan now — see pre-processor)
  var soc = doc.billingSoc || doc.serviceSoc || '';
  if (soc) doc.id = soc.toLowerCase();

  // ── Lines & per-line pricing ─────────────────────────────────────────────
  // eligible_lines/line_prices are parallel arrays (Typesense has no nested-object
  // support on this collection) built from price_by_lines, sorted by line count —
  // eligible_lines doubles as the "number of lines" filter facet.
  var priceByLines = doc.price_by_lines || {};
  var lineCounts = Object.keys(priceByLines).map(Number).filter(function(n) { return n > 0; }).sort(function(a, b) { return a - b; });

  var lineMin = parseInt(doc.minimumLineCount, 10) || 0;
  var lineMax = parseInt(doc.maximumLineCount, 10) || 0;

  if (lineCounts.length > 0) {
    // The per-line table is the authoritative source once present — prefer it
    // over the offer's own stated min/max, which is a display range that can
    // drift from what price_by_lines actually covers.
    lineMin = lineCounts[0];
    lineMax = lineCounts[lineCounts.length - 1];
  }

  doc.eligible_lines = lineCounts.length > 0 ? lineCounts : (lineMin > 0 ? [lineMin] : []);
  doc.line_prices     = doc.eligible_lines.map(function(lc) { return priceByLines[lc] != null ? priceByLines[lc] : priceByLines[String(lc)]; });
  delete doc.price_by_lines; // not a flat/facetable shape — replaced by the two arrays above

  doc.min_lines = lineMin || 1;
  doc.max_lines = lineMax || lineMin || 1;

  // ── Title ─────────────────────────────────────────────────────────────────
  var name = doc.displayName || '';
  if (lineMin === lineMax && lineMin > 0) {
    doc.title = name || 'Plan';
  } else if (lineMin > 0 && lineMax > 0 && name) {
    doc.title = name + ' (' + lineMin + '–' + lineMax + ' lines)';
  } else {
    doc.title = name || 'Plan';
  }

  // ── Price ─────────────────────────────────────────────────────────────────
  // "Starting at" = the cheapest configuration this plan offers (lowest line
  // count), matching standard telecom listing convention — not whichever exact
  // line count happened to be processed last (the bug this pre-processor pass
  // exists to fix; see plans_pre_processor.js).
  doc.price = doc.line_prices.length > 0 ? Math.min.apply(null, doc.line_prices) : 0;

  if (doc.price && doc.min_lines > 0) {
    doc.price_per_line = Math.round((doc.price / doc.min_lines) * 100) / 100;
  }

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

  // ── Plan tier & category ──────────────────────────────────────────────────
  // plan_tier is the internal/chat-facing tag (unchanged); category is the
  // customer-facing filter label shown in the PLP sidebar — was previously
  // hardcoded to the single value "consumerPlans" for every plan via the
  // connector's static_fields (removed), which made the "Category" filter
  // useless (one bucket, all 37 plans). Same tier detection, nicer strings.
  var n = name.toLowerCase();
  if (n.indexOf('beyond') >= 0) {
    doc.plan_tier = 'premium';
    doc.category  = 'Experience Beyond';
  } else if (n.indexOf('more') >= 0) {
    doc.plan_tier = 'standard';
    doc.category  = 'Experience More';
  } else if (n.indexOf('essentials') >= 0 || n.indexOf('ess') >= 0) {
    doc.plan_tier = 'essentials';
    doc.category  = 'Essentials';
  } else if (n.indexOf('value') >= 0) {
    doc.plan_tier = 'value';
    doc.category  = 'Better Value';
  } else {
    doc.plan_tier = 'other';
    doc.category  = 'Other';
  }

  // ── Price range (bucketed facet, same bands used elsewhere in the platform) ─
  var p = doc.price;
  if      (p <= 20)  doc.price_range = '0-20';
  else if (p <= 50)  doc.price_range = '21-50';
  else if (p <= 100) doc.price_range = '51-100';
  else if (p <= 200) doc.price_range = '101-200';
  else if (p <= 500) doc.price_range = '201-500';
  else               doc.price_range = '500+';

  return doc;
}
