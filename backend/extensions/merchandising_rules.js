// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0
// @name Merchandising Rules
// @description Query-aware boost: stamps boost_queries_equal and boost_queries_contains
//   on each document at index time by matching its SKU codes against the rule tables below.
//   Python rankers apply a +0.5 bonus on exact query match and +0.3 on contains match.
//   Assign to any index that needs editorial query pinning; update the rule tables and
//   rebuild whenever merchandising priorities change.
// @run_order 2

/**
 * Rule tables — edit these to control which queries boost which products.
 *
 * BOOST_EQUAL   — fires when the user's full search query exactly equals the rule query.
 *                 Use for high-confidence brand/model searches ("iphone 16", "galaxy s25").
 *
 * BOOST_CONTAINS — fires when the rule query string appears anywhere inside the search.
 *                  Use for broad category terms ("iphone", "samsung", "android").
 *
 * Each rule entry:
 *   query  {string}   — the query term (case-insensitive at match time).
 *   skus   {string[]} — SKU codes of the products to boost for that query.
 *                       Matched against the doc's ``skus`` field (set by the catalog mapper).
 */
var BOOST_EQUAL = [
  //{ query: "iphone 17e", skus: ["194253462309"] },
];

var BOOST_CONTAINS = [
  { query: "iphone", skus: ["195950642827", "195950642872","195950642841","195950642896","195950642810"] },
  //{ query: "samsung", skus: ["SM-S931UZKAXAA"] },
];

/**
 * Stamp ``boost_queries_equal`` and ``boost_queries_contains`` on the document
 * by checking whether its SKU codes appear in any rule entry.
 *
 * @param {Object} doc - Mapped catalog document (output of the catalog mapper extension).
 *   Expected field: ``skus`` (array of SKU code strings).
 * @returns {Object} Same document with ``boost_queries_equal`` and/or
 *   ``boost_queries_contains`` arrays added when at least one rule matches.
 */
function transform(doc) {
  var docSkus = Array.isArray(doc.skus) ? doc.skus : [];
  if (!docSkus.length) return doc;

  var equal = [], contains = [];

  for (var i = 0; i < BOOST_EQUAL.length; i++) {
    var rule = BOOST_EQUAL[i];
    if (!rule || !rule.query || !Array.isArray(rule.skus)) continue;
    for (var j = 0; j < rule.skus.length; j++) {
      if (docSkus.indexOf(rule.skus[j]) >= 0) {
        equal.push(String(rule.query).toLowerCase());
        break;
      }
    }
  }

  for (var i = 0; i < BOOST_CONTAINS.length; i++) {
    var rule = BOOST_CONTAINS[i];
    if (!rule || !rule.query || !Array.isArray(rule.skus)) continue;
    for (var j = 0; j < rule.skus.length; j++) {
      if (docSkus.indexOf(rule.skus[j]) >= 0) {
        contains.push(String(rule.query).toLowerCase());
        break;
      }
    }
  }

  if (equal.length)    doc.boost_queries_equal    = equal;
  if (contains.length) doc.boost_queries_contains = contains;
  return doc;
}
