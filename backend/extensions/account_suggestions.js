// @name Account Suggestions
// @description Generates typeahead search suggestions from account help/support page documents.
// Assign to the Account index (GSheet connector).

/**
 * Generate typeahead suggestions from an account support page document.
 *
 * Uses only the Triggers column — comma-separated search phrases hand-curated
 * per page (e.g. "transfer my number, port-in, port number").
 *
 * Each suggestion carries the source document's url and id so the seeder can
 * create link-mode suggestions (navigate directly) and pin the originating
 * document at position #1 for search-mode suggestions respectively.
 *
 * @param {Object} doc - ISP document from Typesense.
 * @returns {Array<{query: string, url: string|undefined, doc_id: string|undefined}>}
 */
function extractSuggestions(doc) {
  var results = [];
  var seen = {};

  function add(query) {
    var q = String(query || '').trim().toLowerCase();
    if (q && q.length >= 3 && !seen[q]) {
      seen[q] = true;
      // url enables link-mode (navigate directly); doc_id pins this document at
      // position #1 when the suggestion is used in search-mode.
      results.push({ query: q, url: doc.url, doc_id: doc.id });
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
