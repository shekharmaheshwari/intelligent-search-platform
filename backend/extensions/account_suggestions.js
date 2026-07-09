// @name Account Suggestions
// @description Generates typeahead search suggestions from account help/support page documents.
// Assign to the Account index (GSheet connector).

/**
 * Generate typeahead suggestions from an account support page document.
 *
 * Uses only the Triggers column — comma-separated search phrases hand-curated
 * per page (e.g. "transfer my number, port-in, port number").
 *
 * @param {Object} doc - ISP document from Typesense.
 * @returns {Array<{query: string}>}
 */
function extractSuggestions(doc) {
  var results = [];
  var seen = {};

  function add(query) {
    var q = String(query || '').trim().toLowerCase();
    if (q && q.length >= 3 && !seen[q]) {
      seen[q] = true;
      results.push({ query: q });
    }
  }

  var meta = {};
  try { meta = JSON.parse(doc.metadata || '{}'); } catch (e) {}

  var triggers = String(meta.Triggers || meta.triggers || '').trim();
  triggers.split(',').forEach(function(phrase) {
    add(phrase.trim());
  });

  return results;
}
