// @name Plant Suggestions
// @description Generates typeahead search suggestions from plant documents.
// Assign to the Plants index.

/**
 * Generate typeahead suggestions from a plant document.
 *
 * Phrase strategy:
 *   1. Full plant title
 *   2. Plant family name          ("hoya", "philodendron", "snake plant")
 *   3. Family + size              ("hoya 4 inch", "snake plant 6 inch")
 *   4. Care characteristic terms  ("pet safe plant", "beginner plant", "trailing plant")
 *   5. Use-case phrases           ("hanging basket", "terrarium plant", "desk plant")
 *   6. Light need phrases         ("low light plant", "bright indirect plant")
 *   7. Growth style phrases       ("trailing houseplant", "upright houseplant")
 *
 * @param {Object} doc - ISP plant document from Typesense.
 * @returns {Array<{query: string}>}
 */
function extractSuggestions(doc) {
  var results = [];
  var seen    = {};

  function add(query) {
    var q = String(query || '').trim().toLowerCase();
    if (q && q.length >= 3 && !seen[q]) {
      seen[q] = true;
      results.push({ query: q });
    }
  }

  var title       = String(doc.title        || '').trim();
  var family      = String(doc.plant_family || '').trim().toLowerCase();
  var growth      = String(doc.growth_style || '').trim().toLowerCase();
  var difficulty  = String(doc.care_difficulty || '').trim().toLowerCase();
  var petSafe     = doc.pet_safe;   // true | false | null
  var potSize     = String(doc.pot_size || '').trim().toLowerCase();
  var careLight   = String(doc.care_light || '').toLowerCase();
  var careGrowth  = String(doc.care_growth || '').toLowerCase();
  var titleLower  = title.toLowerCase();

  // 1. Full title
  add(title);

  // 2. Plant family as standalone query
  // Map verbose labels → what users actually search
  var familyAliases = {
    'sansevieria':   ['snake plant', 'sansevieria', 'snake plant indoors'],
    'hoya':          ['hoya', 'wax plant'],
    'philodendron':  ['philodendron'],
    'alocasia':      ['alocasia', 'elephant ear plant'],
    'peperomia':     ['peperomia'],
    'calathea':      ['calathea', 'prayer plant'],
    'monstera':      ['monstera', 'split leaf plant', 'monstera deliciosa'],
    'tradescantia':  ['tradescantia', 'spiderwort'],
    'fittonia':      ['fittonia', 'nerve plant'],
    'dracaena':      ['dracaena', 'corn plant'],
    'staghorn fern': ['staghorn fern', 'platycerium'],
    'homalomena':    ['homalomena'],
    'coffee plant':  ['coffee plant', 'coffea arabica'],
    'china doll':    ['china doll plant', 'radermachera']
  };

  var aliases = familyAliases[family] || (family ? [family] : []);
  aliases.forEach(function(a) { add(a); });

  // 3. Family + pot size ("hoya 4 inch", "snake plant 6 inch")
  var sizeMatch = potSize.match(/(\d+)\s*inch/);
  var sizeNum   = sizeMatch ? sizeMatch[1] : '';
  if (sizeNum) {
    aliases.forEach(function(a) { add(a + ' ' + sizeNum + ' inch'); });
  }

  // 4. Care characteristics
  if (petSafe === true)  { add('pet safe plant'); add('pet friendly plant'); add('cat safe plant'); add('dog safe plant'); }
  if (petSafe === false) { add('not pet safe plant'); }

  if (difficulty === 'beginner') { add('beginner plant'); add('easy care plant'); add('low maintenance plant'); }
  if (difficulty === 'moderate') { add('moderate care plant'); }

  // 5. Use-case phrases from pot size and care_growth
  if (potSize.indexOf('hanging') >= 0 || careGrowth.indexOf('trail') >= 0 || careGrowth.indexOf('hanging') >= 0) {
    add('hanging basket plant');
    add('hanging plant indoor');
  }
  if (potSize.indexOf('2') === 0 || titleLower.indexOf('terrarium') >= 0 ||
      titleLower.indexOf('nerve plant') >= 0 || titleLower.indexOf('fittonia') >= 0) {
    add('terrarium plant');
    add('small terrarium plant');
  }
  if (potSize.indexOf('4') === 0 || potSize.indexOf('3') === 0) {
    add('desk plant');
    add('small houseplant');
  }
  if (potSize.indexOf('6') === 0) {
    add('large houseplant');
    add('statement plant');
  }

  // 6. Light need phrases
  if (careLight.indexOf('low') >= 0 || careLight.indexOf('tolerate') >= 0) {
    add('low light plant');
    add('low light houseplant');
    add('office plant low light');
  }
  if (careLight.indexOf('bright') >= 0) {
    add('bright indirect light plant');
    add('indoor plant bright window');
  }

  // 7. Growth style phrases
  if (growth === 'trailing') {
    add('trailing houseplant');
    add('trailing plant indoor');
    add('vining houseplant');
  }
  if (growth === 'upright') {
    add('upright houseplant');
    add('tall indoor plant');
  }
  if (growth === 'compact') {
    add('compact houseplant');
    add('small indoor plant');
  }
  if (growth === 'climbing') {
    add('climbing plant indoor');
    add('climbing houseplant');
  }

  return results;
}
