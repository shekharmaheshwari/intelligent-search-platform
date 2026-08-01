// @name Device Compatibility — Accessories
// @description Extracts specific device model(s) a product fits from its display
//   name (e.g. "... for Samsung Galaxy S24 Ultra") into a structured
//   compatible_devices field, so search/chat can distinguish "fits an S21" from
//   "fits an S24 Ultra" instead of only matching at the brand level.
//   Assign to the Accessories index. Runs after catalog_mapper (run_order 0),
//   before chat_content_accessories.js (run_order 3), so that extension can
//   consume its output.
// @type transform
// @assign none
// @run_order 2

/**
 * Three-tier device-compatibility extraction.
 *
 * Tier 1 (precise): the display name explicitly says "... for <device>",
 *   which covers the large majority of Cases & covers / Screen protectors
 *   (the two categories where compatibility genuinely matters). Handles
 *   multi-device titles like "for iPhone 16 Plus/15 Plus" or
 *   "for iPad Pro 11-inch M5/M4" by propagating the shared prefix across
 *   each "/"-, ","-, or "and"-separated part.
 *
 * Tier 2 (family-level): no "for X" pattern, but a device-family keyword
 *   appears inline (e.g. "Apple Watch Magnetic Fast Charger") — tags the
 *   broader family rather than guessing a specific model that isn't
 *   actually stated anywhere in the source data.
 *
 * Tier 3 (universal): no device reference at all (raw USB-C chargers/cables,
 *   AirPods, Beats, Bose, trackers) — these are honestly device-agnostic,
 *   not missing data, so they get an explicit device_universal flag rather
 *   than a fabricated compatibility claim.
 *
 * Deliberately NOT using any outside/inferred knowledge to guess compatibility
 * for a specific product — only extracting what the product's own display
 * name already states. A wrong AI-guessed compatibility claim is a customer-
 * facing liability; a missing one is just a relevance gap.
 *
 * Input: mapped catalog document (output of catalog_mapper.js). Key field: display.
 * Output: adds compatible_devices (string[]) and device_universal (bool).
 */

var _DEVICE_FAMILY_WORDS = ['iPhone', 'iPad', 'Apple Watch', 'Galaxy', 'Pixel', 'MacBook', 'Motorola', 'moto'];

var _FAMILY_KEYWORDS = [
  ['apple watch', 'Apple Watch'],
  ['iphone',      'iPhone'],
  // Lightning is Apple's proprietary connector (older iPhones/iPads only) and
  // MagSafe wireless charging relies on a magnet ring built only into iPhone 12+ —
  // both are genuinely device-specific, not universal, even without the word
  // "iPhone" in the title (e.g. "Apple MagSafe Charger, 2m").
  ['lightning',   'iPhone'],
  ['magsafe',     'iPhone'],
  ['ipad',        'iPad'],
  ['macbook',     'MacBook'],
  ['galaxy',      'Samsung Galaxy'],
  ['samsung',     'Samsung Galaxy'],
  ['pixel',       'Google Pixel'],
  ['motorola',    'Motorola'],
  ['moto',        'Motorola']
];

function looksLikeFullDevice(chunk) {
  var cl = chunk.toLowerCase();
  for (var i = 0; i < _DEVICE_FAMILY_WORDS.length; i++) {
    if (cl.indexOf(_DEVICE_FAMILY_WORDS[i].toLowerCase()) === 0) return true;
  }
  return false;
}

function familyWordIn(text) {
  var tl = text.toLowerCase();
  for (var i = 0; i < _DEVICE_FAMILY_WORDS.length; i++) {
    if (tl.indexOf(_DEVICE_FAMILY_WORDS[i].toLowerCase()) >= 0) return _DEVICE_FAMILY_WORDS[i];
  }
  return null;
}

function hasBalancedParens(s) {
  var open = (s.match(/\(/g) || []).length;
  var close = (s.match(/\)/g) || []).length;
  return open === close;
}

// Tier 1: parse the "for <device>" suffix, splitting and re-aligning
// multi-device references so abbreviated later parts (e.g. "15 Plus", "M4")
// inherit the right shared prefix from the first part instead of being left
// as dangling fragments.
function extractTier1(display) {
  var m = display.match(/\bfor\s+(.+)$/i);
  if (!m) return null;
  var tail = m[1].replace(/\s+/g, ' ').trim().replace(/\.$/, '');

  var rawParts = tail.split(/\s*,\s*and\s+|\s*\/\s*|\s*,\s*|\s+and\s+/i);
  rawParts = rawParts
    .map(function (p) { return p.trim().replace(/\.$/, ''); })
    .filter(function (p) { return p.length > 0; });

  if (rawParts.length === 0) return null;
  if (rawParts.length === 1) return hasBalancedParens(rawParts[0]) ? rawParts : null;

  var first = rawParts[0];
  var firstTokens = first.split(' ');
  var fam = familyWordIn(first);

  var result = [first];
  for (var i = 1; i < rawParts.length; i++) {
    var part = rawParts[i];
    if (looksLikeFullDevice(part)) {
      result.push(part);
      continue;
    }
    var candTokens = part.split(' ');
    var keepCount = Math.max(1, firstTokens.length - candTokens.length);
    var kept = firstTokens.slice(0, keepCount);
    if (fam && kept.join(' ').toLowerCase().indexOf(fam.toLowerCase()) < 0) {
      kept = [fam];
    }
    result.push(kept.concat(candTokens).join(' '));
  }

  // Safety valve: if any reconstructed part has unbalanced parens, the split
  // logic got confused by an unusual title format — bail out to tier 2/3
  // rather than ship a garbled compatible_devices entry.
  for (var j = 0; j < result.length; j++) {
    if (!hasBalancedParens(result[j])) return null;
  }
  return result;
}

// Tier 2: inline device-family keyword, no explicit "for X" pattern.
function extractTier2(display) {
  var dl = display.toLowerCase();
  for (var i = 0; i < _FAMILY_KEYWORDS.length; i++) {
    if (dl.indexOf(_FAMILY_KEYWORDS[i][0]) >= 0) return _FAMILY_KEYWORDS[i][1];
  }
  return null;
}

function transform(doc) {
  var display = doc.display || doc.title || '';

  if (!display) {
    doc.compatible_devices = [];
    doc.device_universal = true;
    return doc;
  }

  var tier1 = extractTier1(display);
  if (tier1 && tier1.length > 0) {
    doc.compatible_devices = tier1;
    doc.device_universal = false;
    return doc;
  }

  var tier2 = extractTier2(display);
  if (tier2) {
    doc.compatible_devices = [tier2];
    doc.device_universal = false;
    return doc;
  }

  doc.compatible_devices = [];
  doc.device_universal = true;
  return doc;
}
