// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0
// @name Accessory Suggestions
// @description Generates typeahead search suggestions from ISP catalog accessory documents.
// Assign to the Accessories index.

/**
 * Pass-through transform — this extension only implements extractSuggestions.
 * @param {Object} doc
 * @returns {Object}
 */
function transform(doc) {
  return doc;
}

/**
 * Generate typeahead suggestions from an ISP catalog accessory document.
 *
 * Phrase strategy:
 *   1. Generic category terms  ("phone case", "case cover", "screen protector",
 *                               "charger", "adapter")
 *   2. Specialty variants      ("wireless charger", "car charger", "fast charger",
 *                               "usb-c charger", "clear case", "rugged case", etc.)
 *   3. Brand + category        ("zagg screen protector", "mophie charger")
 *   4. Device keyword + short label  — scans "for X" in title for known device keywords
 *      and emits "iphone charger", "iphone case", "samsung charger", "pixel case", etc.
 *   5. Full device brand (first 2 words) + category
 *      ("apple iphone case", "samsung galaxy screen protector")
 *
 * @param {Object} doc - ISP catalog document from Typesense.
 * @returns {Array<{query: string}>}
 */
function extractSuggestions(doc) {
  var results = [];
  var seen = {};

  var title        = String(doc.title || '').trim();
  var manufacturer = String(doc.manufacturer || '').trim().toLowerCase();
  var categorySlug = String(doc.category || '').trim().toLowerCase();
  var titleLower   = title.toLowerCase();

  var categoryMap = {
    'cases-&-covers':      { labels: ['phone case', 'case cover'], short: 'case' },
    'screen-protectors':   { labels: ['screen protector'],          short: 'screen protector' },
    'chargers-&-adapters': { labels: ['charger', 'adapter'],        short: 'charger' }
  };
  var catDef       = categoryMap[categorySlug] || { labels: [], short: '' };
  var primaryLabel = catDef.labels[0] || '';
  var shortLabel   = catDef.short || primaryLabel;

  function add(query) {
    var q = String(query || '').trim().toLowerCase();
    if (q && q.length >= 4 && !seen[q]) {
      seen[q] = true;
      results.push({ query: q });
    }
  }

  // 1. Generic category terms
  catDef.labels.forEach(function(label) { add(label); });

  // 2. Specialty variants
  if (primaryLabel === 'charger') {
    if (titleLower.indexOf('wireless') !== -1) { add('wireless charger'); add('wireless charging'); }
    if (titleLower.indexOf('car')      !== -1) add('car charger');
    if (titleLower.indexOf('fast')     !== -1) add('fast charger');
    if (titleLower.indexOf('usb-c')    !== -1 || titleLower.indexOf('usb c') !== -1) add('usb-c charger');
    if (titleLower.indexOf('cable')    !== -1) add('charging cable');
    if (titleLower.indexOf('car')      !== -1) add('car mount');
  }
  if (primaryLabel === 'phone case') {
    if (titleLower.indexOf('wallet')   !== -1) add('wallet case');
    if (titleLower.indexOf('clear')    !== -1) add('clear case');
    if (titleLower.indexOf('rugged')   !== -1 || titleLower.indexOf('tough') !== -1) add('rugged case');
    if (titleLower.indexOf('magsafe')  !== -1 || titleLower.indexOf('mag safe') !== -1) add('magsafe case');
  }

  // 3. Brand + primary category ("zagg screen protector", "mophie charger")
  if (manufacturer && primaryLabel) add(manufacturer + ' ' + primaryLabel);

  // 4 & 5. Device-specific phrases from "for X" in title
  var forMatch = title.match(/\bfor\s+(.+)$/i);
  if (forMatch && shortLabel) {
    var compatible = forMatch[1].trim();
    var devices = compatible.split(/\s*\/\s*/);

    // Known device keywords users actually type in search
    var deviceKeywords = ['iphone', 'samsung', 'galaxy', 'pixel', 'moto', 'motorola', 'nokia', 'oneplus'];

    devices.forEach(function(device) {
      var d = device.trim().toLowerCase();

      // 4. Device keyword + short label: "iphone charger", "samsung case", "pixel screen protector"
      deviceKeywords.forEach(function(kw) {
        if (d.indexOf(kw) !== -1) add(kw + ' ' + shortLabel);
      });

      // 5. First 2 words of device + short label: "apple iphone case", "samsung galaxy charger"
      var shortName = d.split(/\s+/).slice(0, 2).join(' ');
      if (shortName.length >= 3) add(shortName + ' ' + shortLabel);
    });
  }

  return results;
}
