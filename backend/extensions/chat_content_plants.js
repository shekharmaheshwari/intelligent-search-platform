// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0
// @name Chat Content — Plants
// @description Builds chatContent for plants: pet safety, care difficulty, light/water, growth style, price.
//   Assign to the Plants index. Runs after plants_transformer (run_order 1).
// @type transform
// @assign none
// @run_order 2

/**
 * Add a ``chatContent`` field optimised for the chat RAG pipeline.
 *
 * This is a structured summary targeting the exact intent patterns from the
 * chat improvement plan — pet safety is placed first because it is the
 * highest-specificity attribute query for this index:
 *   - "plants which are pet safe"                 → pet_safety_label
 *   - "easy care plants for beginners"            → care_difficulty
 *   - "trailing plants for a hanging basket"      → growth_style
 *   - "plants that can survive low light"         → care_light
 *   - "cheap plants under $20"                    → price + price_tier
 *   - "what family is this plant from?"           → plant_family
 *
 * Input: enriched plant document (output of plants_transformer.js).
 *   Key fields: title, plant_family, pet_safety_label, pet_safe,
 *               care_difficulty, growth_style, care_light, care_water,
 *               care_humidity, care_temperature, care_growth,
 *               price, price_tier, pot_size.
 */
function transform(doc) {
  var parts = [];

  var title  = doc.title || '';
  var family = doc.plant_family || '';

  // ── Identity ──────────────────────────────────────────────────────────────
  if (title) {
    parts.push(title + (family && family !== 'Other' ? ' (' + family + ' family)' : '') + '.');
  }

  // ── Pet safety — highest priority for attribute-specific retrieval ─────────
  var petLabel = doc.pet_safety_label || 'Unknown';
  if (petLabel === 'Pet safe') {
    parts.push('Pet safety: Pet safe. Safe for cats and dogs.');
  } else if (petLabel === 'Not pet safe') {
    parts.push('Pet safety: Not pet safe. Toxic to cats and dogs.');
  } else {
    parts.push('Pet safety: Unknown.');
  }

  // ── Care difficulty ────────────────────────────────────────────────────────
  var care = doc.care_difficulty || 'easy';
  if (care === 'beginner') {
    parts.push('Care level: Beginner-friendly. Low maintenance, easy care.');
  } else if (care === 'moderate') {
    parts.push('Care level: Moderate. Needs some regular attention.');
  } else {
    parts.push('Care level: Easy.');
  }

  // ── Growth style ─────────────────────────────────────────────────────────
  var growth = doc.growth_style || '';
  if (growth) {
    var growthLabel = growth.charAt(0).toUpperCase() + growth.slice(1);
    parts.push('Growth style: ' + growthLabel + '.');
  }

  // ── Light requirement ─────────────────────────────────────────────────────
  if (doc.care_light) {
    parts.push('Light: ' + doc.care_light + '.');
  }

  // ── Watering ──────────────────────────────────────────────────────────────
  if (doc.care_water) {
    parts.push('Water: ' + doc.care_water + '.');
  }

  // ── Humidity ──────────────────────────────────────────────────────────────
  if (doc.care_humidity) {
    parts.push('Humidity: ' + doc.care_humidity + '.');
  }

  // ── Temperature ───────────────────────────────────────────────────────────
  if (doc.care_temperature) {
    parts.push('Temperature: ' + doc.care_temperature + '.');
  }

  // ── Price ─────────────────────────────────────────────────────────────────
  var price = parseFloat(doc.price) || 0;
  var tier  = doc.price_tier || '';
  if (price > 0) {
    parts.push('Price: $' + price + (tier && tier !== 'free' ? ' (' + tier + ')' : '') + '.');
  }

  // ── Pot size ──────────────────────────────────────────────────────────────
  if (doc.pot_size) parts.push('Pot size: ' + doc.pot_size + '.');

  doc.chatContent = parts.join(' ');
  return doc;
}
