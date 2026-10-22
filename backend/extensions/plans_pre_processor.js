// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0
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

  // billingSoc → shopper-facing highlight strings, from featuresByCategory
  // (richer than featuresByRank: streaming, hotspot, travel, perks).
  var highlightMap = {};
  (response.ratePlanFeatures || []).forEach(function(rpf) {
    if (!rpf || !rpf.soc) return;
    var h = extractHighlights(rpf.featuresByCategory);
    if (h.length) highlightMap[rpf.soc] = h;
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

    var highlights = [];
    for (var h = 0; h < entry.socs.length; h++) {
      if (highlightMap[entry.socs[h]]) { highlights = highlightMap[entry.socs[h]]; break; }
    }

    offer.rankedFeatures = ranked;
    offer.highlight_features = highlights;
    offer.price_by_lines = entry.priceByLines;
    planOffers.push(offer);
  });

  markPriceExtremesForLines(planOffers);

  // Return planOffers as the FIRST key so _auto_detect_path picks it up
  // without needing result_path to be configured.
  return {
    planOffers:       planOffers,
    ratePlans:        response.ratePlans,
    ratePlanFeatures: response.ratePlanFeatures
  };
}

/**
 * Tag each offer with `cheapest_for_lines` / `premium_for_lines`: the line
 * counts at which it is the lowest- / highest-priced plan. Only the
 * pre-processor sees every plan at once — the per-item transform/card-summary
 * extensions cannot make this comparison.
 *
 * Compared within the same customer group (first customerGroups entry, same
 * rule as plans_transformer's customer_group) so a student/military plan is
 * never ranked against consumer plans. A line count with only one plan in the
 * group gets no tag (cheapest and most premium at once is meaningless). Ties
 * tag every tied plan. Zero/missing prices are ignored (the first pass stores
 * 0 for unparseable).
 */
function markPriceExtremesForLines(planOffers) {
  function groupOf(offer) {
    var groups = Array.isArray(offer.customerGroups) ? offer.customerGroups : [];
    return groups.length > 0 ? String(groups[0]).toLowerCase() : 'consumer';
  }

  var stats = {}; // "<group>|<lineCount>" -> {min, max, count}
  planOffers.forEach(function(offer) {
    var group = groupOf(offer);
    Object.keys(offer.price_by_lines).forEach(function(lc) {
      var price = offer.price_by_lines[lc];
      if (!(price > 0)) return;
      var key = group + '|' + lc;
      var s = stats[key] || (stats[key] = { min: price, max: price, count: 0 });
      if (price < s.min) s.min = price;
      if (price > s.max) s.max = price;
      s.count++;
    });
  });

  function linesWhere(offer, pick) {
    var group = groupOf(offer);
    return Object.keys(offer.price_by_lines)
      .filter(function(lc) {
        var price = offer.price_by_lines[lc];
        var s = stats[group + '|' + lc];
        return price > 0 && s && s.count > 1 && s.min !== s.max && price === s[pick];
      })
      .map(Number)
      .filter(function(n) { return n > 0; })
      .sort(function(a, b) { return a - b; });
  }

  planOffers.forEach(function(offer) {
    offer.cheapest_for_lines = linesWhere(offer, 'min');
    offer.premium_for_lines  = linesWhere(offer, 'max');
  });
}

// Feature codes shoppers compare plans on, in display order. Anything whose
// value is "Not included" (or has no shortDescription) is skipped, so each
// plan only lists perks it actually has.
var HIGHLIGHT_CODES = [
  'MGTPST_PREMDATA', 'MGTPST_MH', 'MGTPST_NOU', 'MGTPST_HULU1', 'MGTPST_ATV3',
  'MGTPST_VSQ', 'MGTPST_SATELLITE', 'MGTPST_PG5', 'MGTPST_UPGR',
  'MGTPST_CANMEX', 'MGTPST_SGTD1', 'MGTPST_1YAAA', 'MGTPST_SSP',
  'MGTPST_SLICEFR', 'MGTPST_BTSFN'
];

function extractHighlights(categories) {
  var byCode = {};
  (Array.isArray(categories) ? categories : []).forEach(function(cat) {
    (cat && Array.isArray(cat.features) ? cat.features : []).forEach(function(f) {
      if (!f || !f.code || byCode[f.code]) return;
      var desc = String(f.shortDescription || '').trim();
      if (!desc || /^not included$/i.test(String(f.name || '').trim())) return;
      if (/not (included|available)/i.test(desc)) return;
      byCode[f.code] = desc;
    });
  });
  return HIGHLIGHT_CODES.filter(function(c) { return byCode[c]; })
                        .map(function(c) { return byCode[c]; });
}
