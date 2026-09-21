// @name Plans Pre-Processor
// @description Joins ratePlanFeatures into each ratePlanOffer by billingSoc, and
//   collapses every billingSoc variant of the same conceptual plan (same
//   displayName) into one representative offer carrying the full
//   price-by-line-count table, before per-item transform runs.
// @type pre_processor
// @assign none

/**
 * Cross-reference ratePlanFeatures into each ratePlanOffer, then expose a flat
 * top-level `planOffers` array so result_path configuration is not required.
 *
 * The API response has two sibling arrays that need to be joined:
 *   ratePlans[].ratePlanOffers[]  — the plan items, grouped by exact line count
 *   ratePlanFeatures[]            — feature lists keyed by billingSoc
 *
 * Two separate things collapse into "one document per plan concept" here,
 * both because the source data models the SAME plan as multiple billingSocs:
 *
 * 1. A single billingSoc reappears under every line count its stated
 *    minimumLineCount–maximumLineCount range covers, each time with a
 *    genuinely different price (standard telecom per-line pricing: e.g.
 *    "Experience Beyond 2.0" is $170/mo at 2 lines, $395/mo at 8 lines).
 *
 * 2. The SAME plan (identical displayName, shortDescription, planFeatures,
 *    customerGroups — verified directly against a captured API response) is
 *    split across MULTIPLE billingSocs purely because pricing resets at
 *    different line-count bands — e.g. "Essentials Saver w/ Student Savings
 *    2.0" is billingSoc C26L1SES for exactly 1 line and a completely
 *    different C26L2SES for 2+ lines. Grouping by billingSoc alone (as an
 *    earlier version of this file did) still left every plan indexed 2-3
 *    times under the identical title, differing only by which line-count
 *    band each fragment covered — indexing by billingSoc is a billing-system
 *    identity, not the identity a shopper or the chat bot should see.
 *
 * Grouping by displayName instead fixes both: every (lineCount -> price)
 * pair across every sibling billingSoc merges into one plan's price table,
 * and the shopper/chat-facing catalog gets exactly one document per plan,
 * with an accurate full line-count range and per-line pricing — not the
 * billing system's internal SKU boundaries.
 *
 * After this runs:
 *   - Each offer has a `rankedFeatures` array (read by Plans Transformer)
 *   - Each offer has a `price_by_lines` map: { "<lineCount>": <price>, ... },
 *     merged across every billingSoc that shares its displayName
 *   - `response.planOffers` is the flat list of unique plans (one per displayName)
 *   - `_auto_detect_path` finds `planOffers` as the first key → no result_path needed
 *
 * @param {Object} response  Full raw API response
 * @returns {Object}
 */
function pre_process(response) {
  // Build lookup: billingSoc → featuresByRank string[]
  // featuresByRank items may be strings or objects; extract the label either way.
  var featureMap = {};
  (response.ratePlanFeatures || []).forEach(function(rpf) {
    if (!rpf || !rpf.soc) return;
    featureMap[rpf.soc] = (rpf.featuresByRank || []).map(function(f) {
      if (typeof f === 'string') return f;
      return f.planfeature || f.featureName || f.shortDescription || f.name || f.label || f.description || '';
    }).filter(Boolean);
  });

  // First pass: collect every (lineCount -> price) pair per PLAN NAME across
  // every line-count group and every sibling billingSoc, keeping the
  // first-seen raw offer object (and the ordered list of billingSocs seen)
  // as the representative one to enrich and emit.
  var byName = {}; // displayName -> { offer, priceByLines: {lineCount: price}, socs: [billingSoc, ...] }

  (response.ratePlans || []).forEach(function(rp) {
    var lineCount = rp.lineCount;
    (rp.ratePlanOffers || []).forEach(function(offer) {
      var name = offer.displayName;
      var soc  = offer.billingSoc;
      if (!name || !soc) return;
      if (!byName[name]) {
        byName[name] = { offer: offer, priceByLines: {}, socs: [] };
      }
      if (byName[name].socs.indexOf(soc) === -1) byName[name].socs.push(soc);
      var priceRaw = offer.totalContextPrice !== undefined ? offer.totalContextPrice : offer.totalBasePrice;
      var price = priceRaw !== undefined && priceRaw !== null
        ? (parseFloat(String(priceRaw).replace(/[^0-9.]/g, '')) || 0)
        : 0;
      byName[name].priceByLines[lineCount] = price;
    });
  });

  // Second pass: one representative offer per unique plan NAME, enriched with
  // rankedFeatures (from whichever sibling billingSoc actually has a
  // ratePlanFeatures entry — content is identical across siblings for the
  // same plan, so the first one found is as good as any) and the full,
  // merged per-line price table.
  var planOffers = [];
  Object.keys(byName).forEach(function(name) {
    var entry = byName[name];
    var offer = entry.offer;

    var ranked = [];
    for (var i = 0; i < entry.socs.length; i++) {
      if (featureMap[entry.socs[i]] && featureMap[entry.socs[i]].length) {
        ranked = featureMap[entry.socs[i]];
        break;
      }
    }

    offer.rankedFeatures = ranked;
    offer.price_by_lines = entry.priceByLines;
    planOffers.push(offer);
  });

  // Return planOffers as the FIRST key so _auto_detect_path picks it up
  // without needing result_path to be configured.
  return {
    planOffers:       planOffers,
    ratePlans:        response.ratePlans,
    ratePlanFeatures: response.ratePlanFeatures
  };
}
