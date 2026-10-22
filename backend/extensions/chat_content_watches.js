// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0
// @name Chat Content — Watches
// @description Builds chatContent for smartwatches: GPS, cellular, fitness, compatibility, price.
//   Assign to the Smartwatches index. Runs after catalog_mapper (run_order 1).
// @type transform
// @assign none
// @run_order 3

/**
 * Add a ``chatContent`` field optimised for the chat RAG pipeline.
 *
 * Covers the query types from the chat improvement plan:
 *   - "best smartwatch for running without a phone nearby" → cellular + GPS + fitness
 *   - "will this strap fit Apple Watch Series 9 45mm?"    → compatibility + size
 *   - "cheapest smartwatch"                               → price
 *   - "smartwatch that tracks heart rate"                 → fitness detection
 *
 * GPS and cellular are inferred from the product display name and description
 * because the raw catalog API does not expose these as discrete fields.
 *
 * Input: mapped catalog document (output of catalog_mapper.js).
 *   Key fields: display, manufacturer, operating_system,
 *               price, in_stock, is_featured, description.
 */
function transform(doc) {
  var parts = [];

  var display    = doc.display || doc.title || '';
  var combined   = (display + ' ' + (doc.description || '')).toLowerCase();
  var brand = doc.manufacturer
    ? (String(doc.manufacturer).charAt(0).toUpperCase() + String(doc.manufacturer).slice(1))
    : '';

  // ── Identity ──────────────────────────────────────────────────────────────
  if (display) parts.push(display + (brand ? ' by ' + brand : '') + '.');

  // ── GPS ───────────────────────────────────────────────────────────────────
  var hasGPS = combined.indexOf('gps') >= 0;
  parts.push('GPS: ' + (hasGPS ? 'Yes.' : 'No.'));

  // ── Cellular / LTE ────────────────────────────────────────────────────────
  var hasCellular = combined.indexOf('cellular') >= 0
    || combined.indexOf('+ lte') >= 0
    || combined.indexOf('lte') >= 0
    || combined.indexOf('4g') >= 0;
  if (hasCellular) {
    parts.push('Cellular/LTE: Yes. Works independently without a nearby phone.');
  } else {
    parts.push('Cellular/LTE: No. Requires a nearby phone for calls and data.');
  }

  // ── OS / device compatibility ─────────────────────────────────────────────
  var os = (doc.operating_system || '').toLowerCase();
  if (os.indexOf('watchos') >= 0 || os.indexOf('apple') >= 0 || combined.indexOf('iphone') >= 0) {
    parts.push('Compatible with: iPhone (iOS).');
  } else if (os.indexOf('wear') >= 0 || os.indexOf('android') >= 0) {
    parts.push('Compatible with: Android phones.');
  } else if (doc.operating_system) {
    parts.push('Operating system: ' + doc.operating_system + '.');
  }

  // ── Watch size (from display name) ────────────────────────────────────────
  var sizeMatch = display.match(/(\d+\s*mm)/i);
  if (sizeMatch) parts.push('Case size: ' + sizeMatch[1] + '.');

  // ── Fitness features ─────────────────────────────────────────────────────
  var fitnessKeywords = ['heart rate', 'fitness', 'workout', 'running', 'health', 'ecg', 'blood oxygen', 'sleep', 'exercise'];
  var hasFitness = false;
  for (var i = 0; i < fitnessKeywords.length; i++) {
    if (combined.indexOf(fitnessKeywords[i]) >= 0) { hasFitness = true; break; }
  }
  if (hasFitness) parts.push('Health and fitness tracking: Yes.');

  // ── Colors ────────────────────────────────────────────────────────────────
  var colors = Array.isArray(doc.color_options) ? doc.color_options.filter(Boolean) : [];
  if (colors.length) parts.push('Available in: ' + colors.join(', ') + '.');

  // ── Price, monthly payment & availability ─────────────────────────────────
  var price = doc.price;
  if (price != null && price > 0) {
    var priceStr = 'Price: $' + price + '.';
    if (doc.monthly_payment) priceStr += ' Or $' + doc.monthly_payment + '/mo with financing.';
    parts.push(priceStr);
  } else {
    parts.push('Free with activation.');
  }
  parts.push(doc.in_stock === false ? 'Out of stock.' : 'In stock.');

  // ── Promotions / badges ───────────────────────────────────────────────────
  var badges = Array.isArray(doc.badges) ? doc.badges.filter(Boolean) : [];
  if (badges.length) parts.push('Promotions: ' + badges.join(', ') + '.');

  // ── Product description (health features, battery, specs) ────────────────
  var longDesc = doc.long_description || '';
  if (longDesc) {
    var snippet = longDesc.length > 400 ? longDesc.slice(0, 400).replace(/\s+\S*$/, '') + '…' : longDesc;
    parts.push('Details: ' + snippet);
  }

  // ── Featured flag ─────────────────────────────────────────────────────────
  if (doc.is_featured) parts.push('Featured product.');

  doc.chatContent = parts.join(' ');
  return doc;
}
