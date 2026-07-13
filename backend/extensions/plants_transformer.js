// @name Plants Transformer
// @description Enriches plant documents with a rich content blob, normalized pet_safety, care tags, and search-friendly fields.
// @type transform
// @assign none
// @run_order 1

function transform(doc) {

  // ── Normalize pet safety ────────────────────────────────────────────────────
  var petRaw = (doc.pet_safety || '').toLowerCase();
  if (petRaw.indexOf('not pet') >= 0 || petRaw.indexOf('not safe') >= 0) {
    doc.pet_safe = false;
    doc.pet_safety_label = 'Not pet safe';
  } else if (petRaw.indexOf('pet safe') >= 0 || petRaw.indexOf('considered pet safe') >= 0) {
    doc.pet_safe = true;
    doc.pet_safety_label = 'Pet safe';
  } else {
    doc.pet_safe = null;
    doc.pet_safety_label = 'Unknown';
  }

  // ── Care difficulty tag ─────────────────────────────────────────────────────
  var desc = (doc.content || doc.description || '').toLowerCase();
  var title = (doc.title || '').toLowerCase();
  if (desc.indexOf('beginner') >= 0 || desc.indexOf('easy') >= 0 || desc.indexOf('low-maintenance') >= 0 || desc.indexOf('easygoing') >= 0) {
    doc.care_difficulty = 'beginner';
  } else if (desc.indexOf('moderate') >= 0 || desc.indexOf('humidity') >= 0) {
    doc.care_difficulty = 'moderate';
  } else {
    doc.care_difficulty = 'easy';
  }

  // ── Growth style tag ────────────────────────────────────────────────────────
  var growth = (doc.care_growth || '').toLowerCase();
  if (growth.indexOf('trail') >= 0 || growth.indexOf('vin') >= 0 || growth.indexOf('hanging') >= 0) {
    doc.growth_style = 'trailing';
  } else if (growth.indexOf('climb') >= 0) {
    doc.growth_style = 'climbing';
  } else if (growth.indexOf('rosette') >= 0 || growth.indexOf('compact') >= 0) {
    doc.growth_style = 'compact';
  } else if (growth.indexOf('upright') >= 0) {
    doc.growth_style = 'upright';
  } else {
    doc.growth_style = 'other';
  }

  // ── Plant family tag (from title) ───────────────────────────────────────────
  var families = [
    ['philodendron', 'Philodendron'],
    ['hoya', 'Hoya'],
    ['sansevieria', 'Sansevieria'],
    ['alocasia', 'Alocasia'],
    ['peperomia', 'Peperomia'],
    ['calathea', 'Calathea'],
    ['monstera', 'Monstera'],
    ['tradescantia', 'Tradescantia'],
    ['fittonia', 'Fittonia'],
    ['dracaena', 'Dracaena'],
    ['platycerium', 'Staghorn Fern'],
    ['homalomena', 'Homalomena'],
    ['coffea', 'Coffee Plant'],
    ['radermachera', 'China Doll']
  ];
  doc.plant_family = 'Other';
  for (var i = 0; i < families.length; i++) {
    if (title.indexOf(families[i][0]) >= 0) {
      doc.plant_family = families[i][1];
      break;
    }
  }

  // ── Price tier ──────────────────────────────────────────────────────────────
  var price = parseFloat(doc.price) || 0;
  if      (price === 0)    doc.price_tier = 'free';
  else if (price < 10)     doc.price_tier = 'budget';
  else if (price < 20)     doc.price_tier = 'mid';
  else if (price < 30)     doc.price_tier = 'premium';
  else                     doc.price_tier = 'luxury';

  // ── Rich content blob for full-text search ──────────────────────────────────
  // Combines every meaningful text field so natural-language queries
  // like "pet safe beginner trailing hoya" hit the right documents.
  var parts = [];

  if (doc.title)             parts.push(doc.title);
  if (doc.plant_family && doc.plant_family !== 'Other') parts.push(doc.plant_family);
  if (doc.content || doc.description) parts.push(doc.content || doc.description);

  // Care info
  if (doc.care_light)       parts.push('Light: ' + doc.care_light);
  if (doc.care_water)       parts.push('Water: ' + doc.care_water);
  if (doc.care_growth)      parts.push('Growth: ' + doc.care_growth);
  if (doc.care_humidity)    parts.push('Humidity: ' + doc.care_humidity);
  if (doc.care_temperature) parts.push('Temperature: ' + doc.care_temperature);

  // Pet safety as natural text
  if (doc.pet_safety_label && doc.pet_safety_label !== 'Unknown') {
    parts.push(doc.pet_safety_label);
  }

  // Tags as natural phrases
  parts.push(doc.care_difficulty + ' care');
  parts.push(doc.growth_style + ' plant');
  if (doc.pot_size)         parts.push(doc.pot_size + ' pot');
  if (doc.price_tier)       parts.push(doc.price_tier + ' price');

  doc.content = parts.join('. ');

  return doc;
}
