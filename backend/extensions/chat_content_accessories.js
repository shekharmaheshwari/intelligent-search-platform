// @name Chat Content — Accessories
// @description Builds chatContent for accessories: type, compatibility, price, availability.
//   Assign to the Accessories index. Runs after catalog_mapper (run_order 1).
// @type transform
// @assign none
// @run_order 3

/**
 * Add a ``chatContent`` field optimised for the chat RAG pipeline.
 *
 * Covers the query types from the chat improvement plan:
 *   - "will this watch strap fit Apple Watch Series 9 45mm?" → compatibility + size
 *   - "deals if I buy a case and screen protector together"  → type category
 *   - "I need a charger cable for my phone"                  → type + compatibility
 *   - "cheapest case for Samsung Galaxy"                     → price + brand compat
 *
 * Device compatibility is inferred from the product display name and category
 * because the raw catalog API does not expose a discrete compatibility field.
 *
 * Input: mapped catalog document (output of catalog_mapper.js, type='accessory').
 *   Key fields: display, manufacturer, category, price, in_stock, is_featured.
 */
function transform(doc) {
  var parts = [];

  var display      = doc.display || doc.title || '';
  var displayLower = display.toLowerCase();
  var brand = doc.manufacturer
    ? (String(doc.manufacturer).charAt(0).toUpperCase() + String(doc.manufacturer).slice(1))
    : '';
  var cat = (doc.category || '').replace(/-/g, ' ');

  // ── Identity ──────────────────────────────────────────────────────────────
  if (display) parts.push(display + (brand ? ' by ' + brand : '') + '.');

  // ── Accessory type ────────────────────────────────────────────────────────
  if (cat) parts.push('Type: ' + cat + '.');

  // ── Device compatibility (inferred from name keywords) ────────────────────
  var compat = [];
  var compatKeywords = [
    ['iphone',        'iPhone'],
    ['apple watch',   'Apple Watch'],
    ['apple',         'Apple devices'],
    ['samsung',       'Samsung Galaxy'],
    ['galaxy',        'Samsung Galaxy'],
    ['pixel',         'Google Pixel'],
    ['android',       'Android phones'],
    ['universal',     'Universal — all phones'],
  ];
  var seenCompat = {};
  for (var i = 0; i < compatKeywords.length; i++) {
    var kw = compatKeywords[i][0];
    var label = compatKeywords[i][1];
    if (displayLower.indexOf(kw) >= 0 && !seenCompat[label]) {
      seenCompat[label] = true;
      compat.push(label);
    }
  }
  if (compat.length) parts.push('Compatible with: ' + compat.join(', ') + '.');

  // ── Watch band / size (from display name) ────────────────────────────────
  var sizeMatch = display.match(/(\d+\s*mm)/i);
  if (sizeMatch) parts.push('Size: ' + sizeMatch[1] + '.');

  // ── Colors ────────────────────────────────────────────────────────────────
  var colors = Array.isArray(doc.color_options) ? doc.color_options.filter(Boolean) : [];
  if (colors.length) parts.push('Available in: ' + colors.join(', ') + '.');

  // ── Price & monthly payment ───────────────────────────────────────────────
  var price = doc.price;
  if (price != null && price > 0) {
    var priceStr = 'Price: $' + price + '.';
    if (doc.monthly_payment) priceStr += ' Or $' + doc.monthly_payment + '/mo with financing.';
    parts.push(priceStr);
  } else if (price === 0) {
    parts.push('Free.');
  }
  parts.push(doc.in_stock === false ? 'Out of stock.' : 'In stock.');

  // ── Promotions / badges ───────────────────────────────────────────────────
  var badges = Array.isArray(doc.badges) ? doc.badges.filter(Boolean) : [];
  if (badges.length) parts.push('Promotions: ' + badges.join(', ') + '.');

  // ── Product description (key specs and features) ─────────────────────────
  var longDesc = doc.long_description || '';
  if (longDesc) {
    // Trim to 400 chars so chatContent stays focused
    var snippet = longDesc.length > 400 ? longDesc.slice(0, 400).replace(/\s+\S*$/, '') + '…' : longDesc;
    parts.push('Details: ' + snippet);
  }

  // ── Featured flag ─────────────────────────────────────────────────────────
  if (doc.is_featured) parts.push('Featured accessory.');

  doc.chatContent = parts.join(' ');
  return doc;
}
