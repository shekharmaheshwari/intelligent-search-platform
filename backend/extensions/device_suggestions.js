// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0
// @name Device Suggestions
// @description Generates typeahead search suggestions from ISP catalog device documents.
// Assign to Phones, Tablets, or Smartwatches indexes.

/**
 * Pass-through transform — this extension only implements extractSuggestions.
 * @param {Object} doc
 * @returns {Object}
 */
function transform(doc) {
  return doc;
}

/**
 * Generate typeahead suggestions from an ISP catalog device document.
 * Extracts the product title and any aliases stored in metadata.
 *
 * @param {Object} doc - ISP catalog document from Typesense.
 * @returns {Array<{query: string}>}
 */
function extractSuggestions(doc) {
  var results = [];
  var seen = {};

  function add(query) {
    var q = String(query || '').trim().toLowerCase();
    if (q && !seen[q]) {
      seen[q] = true;
      results.push({ query: q });
    }
  }

  add(doc.title);

  var meta = {};
  try { meta = JSON.parse(doc.metadata || '{}'); } catch (e) {}
  (meta.aliases || []).forEach(function(a) { add(a); });

  return results;
}
