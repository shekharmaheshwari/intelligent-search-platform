// @name Watch Suggestions
// @description Generates typeahead search suggestions from ISP catalog smartwatch documents.
// Assign to the Smartwatches index.

/**
 * Pass-through transform — this extension only implements extractSuggestions.
 * @param {Object} doc
 * @returns {Object}
 */
function transform(doc) {
  return doc;
}

/**
 * Generate typeahead suggestions from an ISP catalog smartwatch document.
 *
 * Phrase strategy:
 *   1. Full product title
 *   2. Brand + "watch" phrases      ("apple watch", "samsung watch", "galaxy watch")
 *   3. Case size                    ("45mm watch", "apple watch 45mm")
 *   4. Connectivity features        ("cellular smartwatch", "gps smartwatch")
 *   5. Health & fitness features    ("fitness tracker watch", "heart rate watch",
 *                                    "blood oxygen watch", "sleep tracking watch")
 *   6. Compatibility phrases        ("iphone compatible smartwatch", "android smartwatch")
 *   7. Color phrases                ("black smartwatch", "midnight smartwatch")
 *   8. Price-tier phrases           ("cheap smartwatch", "budget smartwatch",
 *                                    "smartwatch under $X", "premium smartwatch")
 *   9. Badge-driven phrases         ("free smartwatch", "new smartwatch")
 *   10. Generic always-present terms ("smartwatch", "smart watch", "best smartwatch")
 *
 * @param {Object} doc - ISP catalog document from Typesense (catalog_mapper.js output,
 *   promoted to top-level fields: title/display, manufacturer, operating_system,
 *   category, price, color_options, description, long_description, badges, aliases).
 * @returns {Array<{query: string}>}
 */
function extractSuggestions(doc) {
  var results = [];
  var seen = {};

  var title        = String(doc.title || doc.display || '').trim();
  var titleLower    = title.toLowerCase();
  var manufacturer  = String(doc.manufacturer || '').trim().toLowerCase();
  var category      = String(doc.category || '').trim().toLowerCase();
  var os            = String(doc.operating_system || '').trim().toLowerCase();
  var description   = String(doc.long_description || doc.description || doc.content || '').trim().toLowerCase();
  var combined      = titleLower + ' ' + description;

  // Guard: only run on smartwatch documents so this extension is harmless
  // if ever assigned to a non-watch external index.
  var isWatch = category.indexOf('watch') !== -1 ||
                os.indexOf('watchos') !== -1 ||
                os.indexOf('wear') !== -1 ||
                titleLower.indexOf('watch') !== -1;
  if (!isWatch) return [];

  function add(query) {
    var q = String(query || '').trim().toLowerCase();
    if (q && q.length >= 3 && !seen[q]) {
      seen[q] = true;
      results.push({ query: q });
    }
  }

  // 1. Full title
  add(title);

  // 2. Brand + "watch" phrases
  var brandAliases = {
    'apple':    ['apple watch'],
    'samsung':  ['samsung watch', 'galaxy watch'],
    'google':   ['pixel watch', 'google watch'],
    'garmin':   ['garmin watch'],
    'fitbit':   ['fitbit watch', 'fitbit'],
    'fossil':   ['fossil watch']
  };
  var aliases = brandAliases[manufacturer] || (manufacturer ? [manufacturer + ' watch'] : []);
  aliases.forEach(function(a) { add(a); });

  // 3. Case size ("45mm watch", "apple watch 45mm")
  var sizeMatch = title.match(/(\d+\s*mm)/i);
  if (sizeMatch) {
    var size = sizeMatch[1].replace(/\s+/g, '').toLowerCase();
    add(size + ' watch');
    aliases.forEach(function(a) { add(a + ' ' + size); });
  }

  // 4. Connectivity features
  var hasCellular = combined.indexOf('cellular') !== -1 ||
                     combined.indexOf('lte') !== -1 ||
                     combined.indexOf('+ lte') !== -1 ||
                     combined.indexOf('4g') !== -1 ||
                     combined.indexOf('5g') !== -1;
  if (hasCellular) {
    add('cellular smartwatch');
    add('smartwatch with cellular');
    add('lte smartwatch');
  }
  var hasGPS = combined.indexOf('gps') !== -1;
  if (hasGPS) {
    add('gps smartwatch');
    add('smartwatch with gps');
  }

  // 5. Health & fitness features
  if (combined.indexOf('heart rate') !== -1) add('heart rate watch');
  if (combined.indexOf('fitness') !== -1 || combined.indexOf('workout') !== -1 || combined.indexOf('exercise') !== -1) {
    add('fitness tracker watch');
    add('fitness smartwatch');
  }
  if (combined.indexOf('blood oxygen') !== -1 || combined.indexOf('spo2') !== -1) add('blood oxygen watch');
  if (combined.indexOf('sleep') !== -1) add('sleep tracking watch');
  if (combined.indexOf('ecg') !== -1) add('ecg watch');
  if (combined.indexOf('running') !== -1) add('running watch');
  if (combined.indexOf('waterproof') !== -1 || combined.indexOf('water resistant') !== -1) add('waterproof smartwatch');

  // 6. Compatibility phrases
  if (os.indexOf('watchos') !== -1 || os.indexOf('apple') !== -1 || combined.indexOf('iphone') !== -1) {
    add('iphone compatible smartwatch');
    add('smartwatch for iphone');
  } else if (os.indexOf('wear') !== -1 || os.indexOf('android') !== -1) {
    add('android smartwatch');
    add('smartwatch for android');
  }

  // 7. Color phrases — matched by keyword, not raw string, since some catalog
  // sources (e.g. Apple SKUs) pack the full case+band description into the
  // color name ("Jet Black Aluminum Case Black Sport Band S/M") rather than
  // a plain color word.
  var colorKeywords = [
    'jet black', 'space gray', 'space grey', 'midnight', 'starlight', 'obsidian',
    'moonstone', 'champagne', 'graphite', 'titanium', 'rose gold', 'gold',
    'silver', 'black', 'white', 'gray', 'grey', 'blue', 'green', 'olive',
    'purple', 'pink', 'red', 'cream'
  ];
  var colors = Array.isArray(doc.color_options) ? doc.color_options : [];
  colors.forEach(function(c) {
    var color = String(c || '').trim().toLowerCase();
    for (var ci = 0; ci < colorKeywords.length; ci++) {
      if (color.indexOf(colorKeywords[ci]) !== -1) {
        add(colorKeywords[ci] + ' smartwatch');
        break;
      }
    }
  });

  // 8. Price-tier phrases
  var price = parseFloat(doc.price) || 0;
  if (price > 0) {
    if (price < 150) { add('cheap smartwatch'); add('budget smartwatch'); }
    if (price >= 300) add('premium smartwatch');
    var bucket = Math.ceil(price / 50) * 50;
    add('smartwatch under $' + bucket);
  }

  // 9. Badge-driven phrases
  var badges = Array.isArray(doc.badges) ? doc.badges : [];
  badges.forEach(function(b) {
    var badge = String(b || '').trim().toLowerCase();
    if (badge === 'free') add('free smartwatch');
    if (badge === 'new') add('new smartwatch');
  });

  // 10. Generic always-present terms
  add('smartwatch');
  add('smart watch');
  add('best smartwatch');

  return results;
}
