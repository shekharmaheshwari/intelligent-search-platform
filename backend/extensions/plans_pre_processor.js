// @name Plans Pre-Processor
// @description Joins ratePlanFeatures into each ratePlanOffer by billingSoc before per-item transform runs.
// @type pre_processor
// @assign none

/**
 * Cross-reference ratePlanFeatures into each ratePlanOffer, then expose a flat
 * top-level `planOffers` array so result_path configuration is not required.
 *
 * The API response has two sibling arrays that need to be joined:
 *   ratePlans[].ratePlanOffers[]  — the plan items (indexed as documents)
 *   ratePlanFeatures[]            — feature lists keyed by billingSoc
 *
 * After this runs:
 *   - Each offer has a `rankedFeatures` array (read by Plans Transformer)
 *   - `response.planOffers` is the flat list of all 83 offers across all line counts
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

  // Flatten all offers across all line-count groups, injecting rankedFeatures
  var planOffers = [];
  (response.ratePlans || []).forEach(function(rp) {
    (rp.ratePlanOffers || []).forEach(function(offer) {
      offer.rankedFeatures = featureMap[offer.billingSoc] || [];
      planOffers.push(offer);
    });
  });

  // Return planOffers as the FIRST key so _auto_detect_path picks it up
  // without needing result_path to be configured.
  return {
    planOffers:       planOffers,
    ratePlans:        response.ratePlans,
    ratePlanFeatures: response.ratePlanFeatures
  };
}
