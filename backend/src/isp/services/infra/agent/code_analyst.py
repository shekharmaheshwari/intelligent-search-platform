# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Code analyst: heuristic-based alternative to claude_analyst.

Analyses JSON schema + sample using field-name pattern matching, value
inspection, and cardinality analysis to produce ISP artifacts without
calling any AI model.

Returns the same artifacts dict shape as claude_analyst.analyse(), plus:
  _confidence  (int 0-100) — how confident the heuristics are.
               Used by hybrid mode: if < HYBRID_THRESHOLD, caller falls
               back to claude_analyst and passes these findings as context.
  _method      "code" | "hybrid-code"
"""
from __future__ import annotations
import re
import json

HYBRID_THRESHOLD = 65  # confidence below this triggers Claude fallback in hybrid mode

# ── Field role name patterns ──────────────────────────────────────────────────
_TITLE_NAMES   = {'title', 'name', 'product_name', 'display_name', 'heading',
                  'label', 'item_name', 'product_title', 'full_name', 'headline'}
_CONTENT_NAMES = {'description', 'body', 'body_html', 'summary', 'text',
                  'content', 'details', 'about', 'overview', 'excerpt',
                  'long_description', 'short_description', 'copy'}
_URL_NAMES     = {'url', 'link', 'href', 'handle', 'slug', 'permalink',
                  'product_url', 'canonical_url', 'page_url', 'path'}
_IMAGE_NAMES   = {'image', 'image_url', 'thumbnail', 'photo', 'picture',
                  'img', 'cover', 'avatar', 'hero_image', 'featured_image',
                  'main_image', 'product_image'}
_PRICE_NAMES   = {'price', 'cost', 'amount', 'msrp', 'retail_price',
                  'sale_price', 'unit_price', 'list_price', 'regular_price',
                  'selling_price'}
_ID_NAMES      = {'id', '_id', 'uid', 'uuid', 'product_id', 'item_id',
                  'sku', 'code', 'key', 'ref'}

# Patterns for value inspection
_IMG_URL_RE = re.compile(r'\.(jpg|jpeg|png|gif|webp|svg)(\?|$)', re.I)
_HTTP_RE    = re.compile(r'^https?://')
_HTML_RE    = re.compile(r'<[^>]+>')
_PRICE_RE   = re.compile(r'^\d+(\.\d{1,2})?$')


# ── Public API ────────────────────────────────────────────────────────────────

def analyse(sample: list[dict], schema: dict[str, str], index_name: str) -> dict:
    """
    Heuristic analysis. Returns same shape as claude_analyst.analyse()
    plus ``_confidence`` (int) and ``_method`` (str).
    """
    roles      = _detect_roles(sample, schema)
    confidence = _calc_confidence(roles)
    intent     = _detect_intent(sample, index_name)
    field_cfg  = _build_field_config(sample, schema, roles)

    from isp.services.infra.agent.setup_executor import _build_suggestion_js
    artifacts = {
        "normalizer_js":   _build_normalizer_js(roles, sample),
        "chat_content_js": _build_chat_content_js(roles, intent),
        "suggestion_js":   "",   # always replaced by _build_suggestion_js in executor
        "field_config":    field_cfg,
        "root_key":        "",
        "title_field":     roles.get("title",   {}).get("field", "title"),
        "content_field":   roles.get("content", {}).get("field", "content"),
        "image_field":     roles.get("image",   {}).get("field", "image"),
        "url_field":       roles.get("url",     {}).get("field", "url"),
        "id_field":        roles.get("id",      {}).get("field", "id"),
        "intent_label":    intent,
        "_confidence":     confidence,
        "_method":         "code",
        "_auto_corrections": [],
    }
    return artifacts


def confidence_summary(roles: dict) -> str:
    """Human-readable summary of what was detected, for Claude fallback context."""
    lines = []
    for role, info in roles.items():
        src = info.get("source", "")
        field = info.get("field", "")
        key   = info.get("nested_key", "")
        if src == "nested_array":
            lines.append(f"  {role}: {field}[0].{key}  (nested array)")
        else:
            lines.append(f"  {role}: {field}")
    return "\n".join(lines) if lines else "  (none detected)"


# ── Role detection ────────────────────────────────────────────────────────────

def _detect_roles(sample: list[dict], schema: dict[str, str]) -> dict:
    """
    Return a roles dict:
      { "title": {"field": "title", "source": "top", "confidence": 90}, ... }
    source is "top" (direct field) or "nested_array" (e.g. images[0].src).
    """
    roles: dict[str, dict] = {}

    # ── Pass 1: top-level field names ─────────────────────────────────────────
    top_fields = [k for k in schema if '.' not in k and '[' not in k]

    for field in top_fields:
        fname = field.lower()
        val   = _sample_val(sample, field)

        if fname in _ID_NAMES and "id" not in roles:
            roles["id"] = {"field": field, "source": "top", "confidence": 90}

        if fname in _TITLE_NAMES and "title" not in roles:
            if isinstance(val, str) and val:
                roles["title"] = {"field": field, "source": "top", "confidence": 92}

        if fname in _CONTENT_NAMES and "content" not in roles:
            if isinstance(val, str) and len(val) > 20:
                conf = 90 if _HTML_RE.search(val) else 80
                roles["content"] = {"field": field, "source": "top",
                                    "has_html": bool(_HTML_RE.search(val)),
                                    "confidence": conf}

        if fname in _URL_NAMES and "url" not in roles:
            roles["url"] = {
                "field": field, "source": "top",
                "is_slug": not bool(_HTTP_RE.match(str(val or ""))),
                "confidence": 85,
            }

        if fname in _IMAGE_NAMES and "image" not in roles:
            if isinstance(val, str) and val:
                roles["image"] = {"field": field, "source": "top", "confidence": 88}

        if fname in _PRICE_NAMES and "price" not in roles:
            roles["price"] = {"field": field, "source": "top", "confidence": 85}

    # ── Pass 2: nested arrays — images[].src, variants[].price ───────────────
    # Look for schema keys like  "images[].src"  or  "variants[].price"
    for schema_key in schema:
        if '[' not in schema_key:
            continue
        array_field = schema_key.split('[')[0]   # "images"
        sub_key     = schema_key.split('.')[-1]  # "src"
        af_lower    = array_field.lower()

        # images/photos/media → image
        if "image" not in roles and af_lower in ("images", "photos", "media", "gallery"):
            if sub_key in ("src", "url", "href", "path"):
                roles["image"] = {
                    "field": array_field, "source": "nested_array",
                    "nested_key": sub_key, "confidence": 82,
                }

        # variants/options/tiers → price
        if "price" not in roles and af_lower in ("variants", "options", "tiers", "skus"):
            if "price" in sub_key or sub_key in ("cost", "amount"):
                roles["price"] = {
                    "field": array_field, "source": "nested_array",
                    "nested_key": sub_key, "confidence": 78,
                }

    # ── Pass 3: value-based fallbacks ─────────────────────────────────────────
    # Find image URL in any string field if still not found
    if "image" not in roles:
        for field in top_fields:
            val = _sample_val(sample, field)
            if isinstance(val, str) and (_IMG_URL_RE.search(val) or
               ("cdn" in val and _HTTP_RE.match(val))):
                roles["image"] = {"field": field, "source": "top",
                                  "confidence": 70}
                break

    # Find URL in any string field if still not found
    if "url" not in roles:
        for field in top_fields:
            val = _sample_val(sample, field)
            if isinstance(val, str) and _HTTP_RE.match(val) and \
               not _IMG_URL_RE.search(val):
                roles["url"] = {"field": field, "source": "top",
                                "is_slug": False, "confidence": 65}
                break

    return roles


def _calc_confidence(roles: dict) -> int:
    """
    Score 0-100 based on how many key roles were detected.
    title/id are most important; image/price/url add up.
    """
    weights = {"id": 15, "title": 25, "content": 20,
               "url": 15, "image": 15, "price": 10}
    score = sum(w for role, w in weights.items() if role in roles)
    # Discount by average per-role uncertainty
    if roles:
        avg_conf = sum(r.get("confidence", 80) for r in roles.values()) / len(roles)
        score = int(score * avg_conf / 100)
    return min(score, 100)


# ── JS generation ─────────────────────────────────────────────────────────────

def _build_normalizer_js(roles: dict, sample: list[dict]) -> str:
    lines = ["function transform(doc) {"]

    title_role   = roles.get("title")
    content_role = roles.get("content")
    url_role     = roles.get("url")
    image_role   = roles.get("image")
    price_role   = roles.get("price")
    id_role      = roles.get("id")

    # title
    if title_role and title_role["field"] != "title":
        f = title_role["field"]
        lines.append(f'  doc.title = doc.{f} || doc.title || "";')
    else:
        lines.append('  doc.title = doc.title || "";')

    # content — strip HTML if detected
    if content_role:
        f = content_role["field"]
        if content_role.get("has_html"):
            lines.append(f'  var _raw = doc.{f} || "";')
            lines.append('  doc.content = _raw.replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ").trim();')
        else:
            lines.append(f'  doc.content = doc.{f} || doc.content || "";')
        if f != "content":
            lines.append(f'  delete doc.{f};')
    else:
        lines.append('  doc.content = doc.content || doc.description || doc.body || "";')

    # url
    if url_role:
        f = url_role["field"]
        if url_role.get("is_slug"):
            lines.append(f'  doc.url = doc.{f} ? ("/products/" + doc.{f}) : (doc.url || "");')
        else:
            lines.append(f'  doc.url = doc.{f} || doc.url || "";')
        if f not in ("url",):
            lines.append(f'  // original field "{f}" kept as-is for reference')
    else:
        lines.append('  doc.url = doc.url || doc.link || doc.href || "";')

    # image — top-level or nested array
    if image_role:
        f = image_role["field"]
        if image_role["source"] == "nested_array":
            nk = image_role["nested_key"]
            lines.append(f'  doc.image = (doc.{f} && doc.{f}.length && doc.{f}[0]) '
                         f'? (doc.{f}[0].{nk} || "") : (doc.image || "");')
        else:
            lines.append(f'  doc.image = doc.{f} || "";')
    else:
        lines.append('  doc.image = doc.image || doc.thumbnail || doc.photo || "";')

    # price — top-level or nested array
    if price_role:
        f = price_role["field"]
        if price_role["source"] == "nested_array":
            nk = price_role["nested_key"]
            lines.append(f'  doc.price = parseFloat((doc.{f} && doc.{f}.length && doc.{f}[0]) '
                         f'? (doc.{f}[0].{nk} || 0) : (doc.price || 0));')
        else:
            lines.append(f'  doc.price = parseFloat(doc.{f} || 0);')
    else:
        lines.append('  doc.price = parseFloat(doc.price || 0);')

    # in_stock
    lines.append('  if (doc.available !== undefined) { doc.in_stock = !!doc.available; }')
    lines.append('  else if (doc.in_stock !== undefined) { doc.in_stock = !!doc.in_stock; }')
    lines.append('  else { doc.in_stock = true; }')

    # Flatten known array patterns from sample
    _append_array_handling(lines, sample, roles)

    lines.append("  return doc;")
    lines.append("}")
    return "\n".join(lines)


def _append_array_handling(lines: list[str], sample: list[dict], roles: dict) -> None:
    """Detect array fields in sample[0] and emit flattening code for each."""
    if not sample:
        return
    doc = sample[0]
    skip_fields = {"vec", "indexed_at", "source_type"}
    image_field = roles.get("image", {}).get("field", "")
    price_field = roles.get("price", {}).get("field", "")

    for key, val in doc.items():
        if key in skip_fields:
            continue
        if isinstance(val, list):
            if not val:
                lines.append(f'  doc.{key} = "";')
            elif isinstance(val[0], str):
                # Array of strings → join
                lines.append(f'  doc.{key} = (doc.{key} || []).join(", ");')
            elif isinstance(val[0], dict):
                # Array of objects — always flatten to string, even if a scalar
                # was already extracted (price from variants[0].price, image from images[0].src).
                # Without this, variants/images remain as list-of-dicts and get packed
                # into the metadata blob as nested objects, which display as [object Object].
                first = val[0]
                if key == image_field:
                    # Use src/url key to build a comma-joined image URL list
                    img_key = next((k for k in ("src", "url", "href") if k in first), None)
                    if img_key:
                        lines.append(
                            f'  doc.{key} = (doc.{key} || []).map(function(x){{ '
                            f'return x.{img_key} || ""; }}).filter(Boolean).join(", ");'
                        )
                    else:
                        lines.append(f'  doc.{key} = "";')
                    continue
                # Pick the most meaningful scalar key from the first object
                pick = next((k for k in ("title", "name", "label", "value", "text")
                             if k in first), None)
                if pick:
                    lines.append(
                        f'  doc.{key} = (doc.{key} || []).map(function(x){{ '
                        f'return x.{pick} || ""; }}).filter(Boolean).join(", ");'
                    )
                else:
                    # Fallback: join all scalar values of first key
                    first_key = list(first.keys())[0]
                    lines.append(
                        f'  doc.{key} = (doc.{key} || []).map(function(x){{ '
                        f'return String(x.{first_key} || ""); }}).filter(Boolean).join(", ");'
                    )


def _build_chat_content_js(roles: dict, intent: str) -> str:
    """Build chatContent from detected fields."""
    parts = []
    field_map = {
        "title":   ("Title",   roles.get("title",   {}).get("field", "title")),
        "price":   ("Price",   "price"),
        "content": ("Details", "content"),
    }
    lines = ["function transform(doc) {"]
    lines.append('  var parts = [];')
    lines.append('  if (doc.brand) parts.push("Brand: " + doc.brand + ".");')
    lines.append(f'  if (doc.price) parts.push("Price: $" + doc.price + ".");')
    lines.append('  if (doc.product_type || doc.category) '
                 'parts.push("Type: " + (doc.product_type || doc.category) + ".");')
    lines.append('  if (doc.content) parts.push(doc.content.substring(0, 300));')
    lines.append('  doc.chatContent = parts.join(" ");')
    lines.append("  return doc;")
    lines.append("}")
    return "\n".join(lines)


# ── Field config ──────────────────────────────────────────────────────────────

def _build_field_config(sample: list[dict], schema: dict[str, str],
                        roles: dict) -> list[dict]:
    """
    Build field_config array.
    - Always include ISP standard fields.
    - Low-cardinality string fields → facet.
    - High-cardinality strings → searchable.
    - Numeric → float/int, no facet.
    - Bool → bool, facet.
    """
    # Cardinality per field across sample
    cardinality: dict[str, set] = {}
    for item in sample:
        for k, v in item.items():
            if isinstance(v, (str, int, float, bool)):
                cardinality.setdefault(k, set()).add(str(v))

    n = max(len(sample), 1)
    config: list[dict] = []
    seen: set[str] = set()

    # ISP standard fields first
    for fc in [
        {"name": "title",       "type": "string", "searchable": True,  "facet": False},
        {"name": "content",     "type": "string", "searchable": True,  "facet": False},
        {"name": "chatContent", "type": "string", "searchable": True,  "facet": False},
        {"name": "url",         "type": "string", "searchable": False, "facet": False},
        {"name": "image",       "type": "string", "searchable": False, "facet": False},
        {"name": "price",       "type": "float",  "searchable": False, "facet": False},
        {"name": "in_stock",    "type": "bool",   "searchable": False, "facet": True},
    ]:
        config.append(fc)
        seen.add(fc["name"])

    # Extra domain fields
    skip = {"vec", "indexed_at", "source_type", "id", "body_html", "description",
            "body", "handle", "image_url", "thumbnail"}
    for field, ftype in schema.items():
        if '.' in field or '[' in field:
            continue
        if field in seen or field in skip:
            continue
        unique = len(cardinality.get(field, set()))
        ratio  = unique / n

        if "bool" in ftype or field.startswith("is_") or field.startswith("has_"):
            fc = {"name": field, "type": "bool",   "searchable": False, "facet": True}
        elif ftype in ("int", "float") or "price" in field or "count" in field:
            fc = {"name": field, "type": "float",  "searchable": False, "facet": False}
        elif ftype == "string":
            # Low cardinality → facet; high → searchable
            is_facet     = unique <= max(5, n * 0.4)
            is_searchable = ratio > 0.3 and unique > 3
            fc = {"name": field, "type": "string",
                  "searchable": is_searchable, "facet": is_facet}
        else:
            fc = {"name": field, "type": "string", "searchable": False, "facet": False}

        config.append(fc)
        seen.add(field)

    return config


# ── Intent label ──────────────────────────────────────────────────────────────

def _detect_intent(sample: list[dict], index_name: str) -> str:
    # Check product_type field in sample
    if sample:
        pt = sample[0].get("product_type") or sample[0].get("type") or \
             sample[0].get("category") or ""
        if pt and isinstance(pt, str) and len(pt) < 30:
            # Take last word and lowercase
            return pt.strip().split()[-1].lower().rstrip('s')

    # Fall back to last word of index name
    words = re.split(r'[\s_\-]+', index_name.strip().lower())
    return words[-1].rstrip('s') if words else "item"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sample_val(sample: list[dict], field: str):
    """Return first non-null value for field across sample items."""
    for item in sample:
        v = item.get(field)
        if v not in (None, "", [], {}):
            return v
    return None
