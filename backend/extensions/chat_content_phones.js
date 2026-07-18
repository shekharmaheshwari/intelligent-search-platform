// @name Chat Content — Phones
// @description Builds chatContent for phones/tablets: brand, OS, form factor, price, availability, colors.
//   Assign to the Phones (and Tablets) index. Runs after catalog_mapper (run_order 1) and version_score (run_order 1).
// @type transform
// @assign none
// @run_order 3

/**
 * Add a ``chatContent`` field optimised for the chat RAG pipeline.
 *
 * Covers the query types from the chat improvement plan:
 *   - "Need a new iPhone"         → brand + display name
 *   - "lowest price phone"        → price
 *   - "best battery for travel"   → form factor + OS (spec data lives in description when available)
 *   - "should I get X or Y?"      → price + OS + form factor for comparison table
 *   - "free phones"               → price === 0 → "Free with activation"
 *   - "out of stock" handling     → explicit in_stock sentence
 *
 * Input: mapped catalog document (output of catalog_mapper.js).
 *   Key fields: display, manufacturer, operating_system, form_factor,
 *               price, in_stock, color_options, is_featured, description.
 */
function transform(doc) {
  var parts = [];

  var display = doc.display || doc.title || '';
  var brand = doc.manufacturer
    ? (String(doc.manufacturer).charAt(0).toUpperCase() + String(doc.manufacturer).slice(1))
    : '';

  // ── Identity ──────────────────────────────────────────────────────────────
  if (display) {
    parts.push(display + (brand ? ' by ' + brand : '') + '.');
  }

  // ── Operating system ──────────────────────────────────────────────────────
  var os = doc.operating_system || '';
  if (os) parts.push('Operating system: ' + os + '.');

  // ── Form factor ───────────────────────────────────────────────────────────
  var form = doc.form_factor || 'standard';
  if      (form === 'fold') parts.push('Form factor: Foldable phone.');
  else if (form === 'flip') parts.push('Form factor: Flip phone.');
  else                      parts.push('Form factor: Standard smartphone.');

  // ── Price & availability ──────────────────────────────────────────────────
  var price = doc.price;
  if (price != null && price > 0) {
    parts.push('Price: $' + price + '.');
  } else {
    parts.push('Free with activation.');
  }
  parts.push(doc.in_stock === false ? 'Out of stock.' : 'In stock.');

  // ── Colors ────────────────────────────────────────────────────────────────
  var colors = Array.isArray(doc.color_options) ? doc.color_options.filter(Boolean) : [];
  if (colors.length) parts.push('Available in: ' + colors.join(', ') + '.');

  // ── Monthly payment ───────────────────────────────────────────────────────
  if (doc.monthly_payment) parts.push('Financing: $' + doc.monthly_payment + '/mo.');

  // ── Promotions / badges ───────────────────────────────────────────────────
  var badges = Array.isArray(doc.badges) ? doc.badges.filter(Boolean) : [];
  if (badges.length) parts.push('Promotions: ' + badges.join(', ') + '.');

  // ── Product description (specs, battery, camera, etc.) ───────────────────
  var longDesc = doc.long_description || '';
  if (longDesc) {
    var snippet = longDesc.length > 400 ? longDesc.slice(0, 400).replace(/\s+\S*$/, '') + '…' : longDesc;
    parts.push('Details: ' + snippet);
  }

  // ── Featured flag ─────────────────────────────────────────────────────────
  if (doc.is_featured) parts.push('Featured device.');

  doc.chatContent = parts.join(' ');
  return doc;
}
