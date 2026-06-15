// @name Version Score
// @description Extracts a recency signal from the product title and applies a bonus for items carrying a "NEW" badge. Runs after the catalog mapper so doc.display is available.
// @assign all
// @run_order 1

/**
 * Compute a version recency score and a new-badge boost, then attach them to the
 * document as ``version_score`` so the search-time scorer can apply a small tiebreaker.
 *
 * Scoring rules:
 *   - Year in title (202x):  (year - 2019) / 10        e.g. 2024 → 0.5
 *   - Model number after a letter (e.g. "iPhone 16"):   n / 100   e.g. 16 → 0.16
 *   - "NEW" badge present:   +0.5 additive bonus
 *
 * Unit suffixes (GB, TB, mAh, W, MHz, GHz, mm, MP, Hz) are excluded from the
 * model-number match so storage/spec numbers do not accidentally inflate the score.
 *
 * @param {Object} doc - Mapped product document (output of the catalog mapper extension).
 *   Expected fields: ``display`` (product name), ``badges`` (array of {label} objects).
 * @returns {Object} Same document with ``version_score`` set when a signal is found.
 */
function transform(doc) {
  var title = doc.display || doc.familyName || doc.name || doc.title || '';
  var vs = 0.0;

  // Year-based: "2024", "2023", etc. – most reliable version signal
  var yearMatch = title.match(/\b(202\d)\b/);
  if (yearMatch) {
    vs = (parseInt(yearMatch[1], 10) - 2019) / 10.0;
  } else {
    // Model number: a letter (or letter-space) followed by 1-2 digits at a word boundary.
    // Avoid unit suffixes immediately after the match.
    var unitRe = /^\s*(GB|TB|mAh|MHz|GHz|mm|MP|Hz|[KkWw]\b|in\b|ft\b)/;
    var tokenRe = /[A-Za-z][ -]?(\d{1,2})\b/g;
    var m;
    while ((m = tokenRe.exec(title)) !== null) {
      var n = parseInt(m[1], 10);
      if (n < 1) continue;
      var after = title.slice(m.index + m[0].length);
      if (unitRe.test(after)) continue;
      vs = n / 100.0;
      break;
    }
  }

  // Badge boost: "NEW" label signals a freshly launched product.
  // Kept at 0.5 (same order of magnitude as a year signal) so that, after
  // _VERSION_WEIGHT (×0.002) and the ranker cap (0.005), a NEW item beats an
  // older sibling in a tie but cannot outrank a genuinely better keyword match.
  var badges = doc.badges || [];
  for (var i = 0; i < badges.length; i++) {
    if (badges[i] && String(badges[i].label || '').toUpperCase() === 'NEW') {
      vs += 0.5;
      break;
    }
  }

  if (vs > 0) {
    doc.version_score = Math.round(vs * 10000) / 10000;
  }
  return doc;
}
