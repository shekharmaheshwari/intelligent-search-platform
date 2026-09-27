// @name Card Summary — Plants
// @description Builds cardSummary for plants: a short, display-safe care/spec blurb for
//   direct customer-facing rendering (e.g. cards-only chat mode with no LLM call) — none
//   of this is shown by a plain product card today, so it's kept close to chatContent's
//   own content, minus the identity/price restated there. Assign to the Plants index.
//   Runs after plants_transformer (run_order 1).
// @type transform
// @assign none
// @run_order 2

/**
 * Add a ``cardSummary`` field: plain text, meant to be shown as-is next to
 * a plant card — not fed to an LLM (that's chatContent's job).
 *
 * Input: enriched plant document (output of plants_transformer.js).
 *   Key fields: pet_safety_label, care_difficulty, growth_style, care_light,
 *               care_water, care_humidity, care_temperature, pot_size.
 */
function transform(doc) {
  var parts = [];

  // ── Pet safety — highest priority for attribute-specific display ─────────
  var petLabel = doc.pet_safety_label || 'Unknown';
  if (petLabel === 'Pet safe') parts.push('Pet safe.');
  else if (petLabel === 'Not pet safe') parts.push('Not pet safe — toxic to cats and dogs.');

  // ── Care difficulty ────────────────────────────────────────────────────────
  var care = doc.care_difficulty || 'easy';
  if (care === 'beginner') parts.push('Beginner-friendly, low maintenance.');
  else if (care === 'moderate') parts.push('Moderate care — needs regular attention.');
  else parts.push('Easy care.');

  // ── Growth style ─────────────────────────────────────────────────────────
  var growth = doc.growth_style || '';
  if (growth) parts.push(growth.charAt(0).toUpperCase() + growth.slice(1) + ' growth.');

  // ── Light / water / humidity / temperature ────────────────────────────────
  if (doc.care_light)       parts.push('Light: ' + doc.care_light + '.');
  if (doc.care_water)       parts.push('Water: ' + doc.care_water + '.');
  if (doc.care_humidity)    parts.push('Humidity: ' + doc.care_humidity + '.');
  if (doc.care_temperature) parts.push('Temperature: ' + doc.care_temperature + '.');

  // ── Pot size ──────────────────────────────────────────────────────────────
  if (doc.pot_size) parts.push('Pot size: ' + doc.pot_size + '.');

  doc.cardSummary = parts.join(' ');
  return doc;
}
