// @name Catalog Mapper
// @description Maps REST API product response fields to the ISP catalog schema. Assign to Phones, Accessories, Smartwatches, and Tablets indexes.

/**
 * Transform a raw product catalog item into the ISP catalog schema.
 *
 * Field extraction strategy (in priority order):
 * - Price: top-level price fields → first SKU price fields → first SKU frpPrice object.
 * - Images: FrontImage/LeftImage/ThumbNail URLs from the first three SKUs, deduplicated.
 * - Category: category/productCategory/familyCategory/subcategory, normalised to a string.
 * - Availability: boolean, status-object, or string — parsed to a single boolean.
 * - URL: constructed from the correct base path (cell-phone vs accessory) + urlEncodedFamilyName.
 *
 * @param {Object} doc - Raw item object from the product REST API response.
 *   The ``_isp_type`` field is injected by the ingest pipeline before this function runs;
 *   it is ``"device"`` for phones/tablets and ``"accessory"`` for accessories.
 * @returns {Object} Document shaped to the ISP catalog schema expected by
 *   ``_generic_catalog_mapper`` in ``routers/indexes.py``.
 */
function slugify(str) {
  return String(str).toLowerCase().replace(/\s+/g, '-');
}

function transform(doc) {
  var type = doc._isp_type || 'device';
  var skus = (doc.skus || []).filter(function(s) { return s && typeof s === 'object'; });

  // Price: top-level fields, then first SKU fields, then SKU frpPrice
  var priceFields = ['price', 'msrp', 'basePrice', 'fullRetailPrice', 'retailPrice', 'salePrice'];
  var price = null;
  for (var i = 0; i < priceFields.length && price === null; i++) {
    if (doc[priceFields[i]] != null) price = parseFloat(doc[priceFields[i]]);
  }
  if (price === null && skus.length > 0) {
    var sku0 = skus[0];
    for (var i = 0; i < priceFields.length && price === null; i++) {
      if (sku0[priceFields[i]] != null) price = parseFloat(sku0[priceFields[i]]);
    }
    if (price === null) {
      var frp = sku0.frpPrice || {};
      var frpFields = ['salePrice', 'listPrice', 'displaySalePrice', 'displayListPrice'];
      for (var i = 0; i < frpFields.length && price === null; i++) {
        if (frp[frpFields[i]] != null) price = parseFloat(frp[frpFields[i]]);
      }
    }
  }

  // Images from skus (FrontImage preferred)
  var validImgTypes = { FrontImage: 1, LeftImage: 1, ThumbNail: 1 };
  var images = [], seenImgs = {};
  skus.slice(0, 3).forEach(function(sku) {
    (sku.images || []).forEach(function(img) {
      if (img && img.url && validImgTypes[img.imgtype] && !seenImgs[img.url]) {
        seenImgs[img.url] = true;
        images.push(img.url);
      }
    });
  });

  // Category (may be a string or object) — stored as a URL-friendly slug
  var catRaw = doc.category || doc.productCategory || doc.familyCategory || doc.subcategory || null;
  var catName = typeof catRaw === 'object' && catRaw
    ? (catRaw.name || (type === 'device' ? 'Smartphones' : 'Accessories'))
    : (catRaw || (type === 'device' ? 'Smartphones' : 'Accessories'));
  var category = slugify(catName);

  // Availability
  var avail = doc.availability || doc.stock || doc.inventoryStatus;
  var inStock = true;
  if (avail != null) {
    if (typeof avail === 'boolean') {
      inStock = avail;
    } else if (typeof avail === 'object' && avail) {
      var st = String(avail.status || '').toUpperCase();
      inStock = st.indexOf('OUT') < 0 && st !== 'UNAVAILABLE' && st !== 'DISCONTINUED';
    } else {
      inStock = String(avail).toUpperCase().indexOf('OUT') < 0;
    }
  }

  // Core fields
  var manufacturer = slugify(doc.manufacturer || doc.brand || '');
  var osName       = doc.operatingSystem || '';
  var display      = doc.familyName || doc.name || doc.title || doc.display || '';

  // Derive form_factor from display name (devices only).
  // Source APIs rarely expose this field directly so we infer it from keywords.
  // fold checked before flip/razr so "razr fold" resolves to fold, not flip.
  var form_factor = '';
  if (type === 'device') {
    var tl = display.toLowerCase();
    if      (tl.indexOf('fold') >= 0)                               form_factor = 'fold';
    else if (tl.indexOf('flip') >= 0 || tl.indexOf('razr') >= 0)   form_factor = 'flip';
    else                                                             form_factor = 'standard';
  }
  var rank         = parseInt(doc.deviceRank || doc.rank || '999', 10);
  var rawId        = doc.familyCode || doc.sku || doc.id || '';
  var urlSlug      = doc.urlEncodedFamilyName || doc.urlName || doc.url_name || '';
  var base         = type === 'device'
    ? 'https://www.example.com/cell-phone'
    : 'https://www.example.com/accessory';

  // Tags & aliases
  var aliases = manufacturer && display
    ? [(manufacturer + ' ' + display.toLowerCase())] : [];
  var tagSet = {};
  var rawTags = type === 'device'
    ? [manufacturer, osName.toLowerCase(), 'phone', 'smartphone']
    : [manufacturer, category];
  var tags = rawTags.filter(function(t) {
    if (!t || tagSet[t]) return false;
    tagSet[t] = true; return true;
  });

  var skuCodes   = skus.slice(0, 5).map(function(s) { return s.skuCode || ''; }).filter(Boolean);
  var colorNames = (doc.colorOptions || []).map(function(c) { return c && c.name ? c.name : ''; }).filter(Boolean);

  // Badges live per-SKU in the product API, not at product level — collect and deduplicate.
  // Extract label strings only (not the full object) so metadata serializes cleanly.
  var badgeSet = {}, badges = [];
  skus.forEach(function(sku) {
    (sku.badges || []).forEach(function(b) {
      if (b && b.label && !badgeSet[b.label]) { badgeSet[b.label] = true; badges.push(b.label); }
    });
  });
  // Also include any top-level badges the API may add in future.
  (doc.badges || []).forEach(function(b) {
    if (b && b.label && !badgeSet[b.label]) { badgeSet[b.label] = true; badges.push(b.label); }
  });

  var shortDesc = display + (manufacturer ? ' by ' + manufacturer + '.' : '.') +
                  (osName ? ' ' + osName + ' smartphone.' : '');
  var longDesc  = doc.longDescription || '';

  // Monthly payment from the first CRP entry (phones/accessories only)
  var monthlyPayment = null;
  var crp = skus.length > 0 ? (skus[0].crpPrices || []) : [];
  if (crp.length > 0 && crp[0].monthlyPayment) {
    var mp = crp[0].monthlyPayment;
    monthlyPayment = parseFloat(mp.salePrice || mp.listPrice || 0) || null;
  }

  return {
    id:               rawId,
    display:          display,
    type:             type,
    url:              urlSlug ? base + '/' + urlSlug : '',
    url_name:         urlSlug,
    manufacturer:     manufacturer,
    operating_system: osName,
    form_factor:      form_factor,
    category:         category,
    price:            price,
    in_stock:         inStock,
    images:           images.slice(0, 3),
    color_options:    colorNames,
    skus:             skuCodes,
    description:      shortDesc,
    // content = long description when available; falls back to short desc.
    // This is the primary searchable body field indexed by Typesense BM25.
    content:          longDesc || shortDesc,
    long_description: longDesc,
    monthly_payment:  monthlyPayment,
    aliases:          aliases,
    tags:             tags,
    popularity:       Math.max(0, 1000 - rank * 10),
    is_featured:      rank <= 5,
    // Badge label strings — passed through so chained extensions can use them.
    badges:           badges
  };
}
