# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
REST API connector: fetch, parse, and normalise API responses into ISP documents.

Two public entry points:
- ``fetch_raw_items`` — returns the raw dicts from the API with no field renaming.
  Used by the catalog write-back path so that JS extensions see the original fields.
- ``fetch_and_parse`` — applies ``field_map`` renames and heuristic field detection
  to produce ``DocumentItem``-compatible dicts.  Used for ISP-managed indexes.

Field detection heuristics (``_auto_detect_fields``) work on a sample document
and look for common naming patterns.  They are a fallback when the user has not
configured explicit field mappings; always prefer explicit ``field_map`` or
``id_field``/``title_field``/``content_field``/``url_field`` over relying on them.
"""
from __future__ import annotations
import hashlib, json, logging, time
import httpx
from isp.models import RestConnectorConfig

log = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _auto_detect_path(data: dict | list) -> tuple[str, list]:
    """If the top-level response is a dict, find the first non-empty array value."""
    if isinstance(data, list):
        return ("", data)
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list) and v:
                return (k, v)
    return ("", [data] if isinstance(data, dict) else [])


def _auto_detect_fields(item: dict) -> dict[str, str]:
    """Pick id / title / content / url fields heuristically from a sample document."""
    keys = [k for k, v in item.items() if isinstance(v, (str, int, float)) and v != ""]

    def pick(*patterns: str) -> str:
        """Return the first key whose lowercase name contains any of the given patterns."""
        for p in patterns:
            for k in keys:
                if p in k.lower() and item.get(k):
                    return k
        return ""

    return {
        "id_field":      pick("id", "code", "sku", "key", "uuid") or (keys[0] if keys else "id"),
        "title_field":   pick("name", "title", "display", "label", "family") or (keys[1] if len(keys) > 1 else ""),
        "content_field": pick("description", "content", "body", "text", "manufacturer", "brand", "summary", "detail"),
        "url_field":     pick("url", "link", "href", "slug", "path"),
    }


def _extract_path(data: dict | list, path: str) -> list:
    """
    Navigate a dot-separated path through nested dicts/lists and return the value as a list.

    Example: ``_extract_path({"results": {"items": [...]}}, "results.items")``
    Returns the list at that path, or an empty list if any segment is missing.

    When a path segment is accessed on a list and every element of that list
    returns another list-of-dicts, the results are flattened one level so callers
    always get a flat list of items.  This handles paths like
    ``"ratePlans.ratePlanOffers"`` where each ratePlan contains its own offers
    array — without mishandling APIs whose top-level data happens to be a
    list-of-lists (those would have non-dict inner items and are left untouched).
    """
    import itertools
    if not path:
        return data if isinstance(data, list) else [data]
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part, [])
        elif isinstance(data, list):
            expanded = [item.get(part) for item in data if isinstance(item, dict)]
            # Flatten only when expanding a list field that itself contains lists
            # of dicts — i.e. a genuine nested-array traversal, not native list data.
            if (
                expanded
                and all(isinstance(x, list) for x in expanded)
                and all(isinstance(i, dict) for x in expanded for i in x if x)
            ):
                data = list(itertools.chain.from_iterable(x for x in expanded if x))
            else:
                data = expanded
        else:
            return []
    return data if isinstance(data, list) else [data]


def _apply_field_map(item: dict, field_map: dict[str, str]) -> dict:
    """Rename source fields per field_map. Empty-string target = drop the field."""
    if not field_map:
        return item
    result = {}
    for source_key, value in item.items():
        target_key = field_map.get(source_key, source_key)
        if target_key:  # empty string means drop
            result[target_key] = value
    return result


def _apply_value_map(doc: dict, value_map: dict[str, dict[str, str]]) -> dict:
    """
    Replace field values according to value_map after field_map renames have been applied.

    Only fields that are present in the document and whose string representation
    matches an inner map key are rewritten; everything else passes through unchanged.
    This must be called *after* ``_apply_field_map`` so that the outer keys refer to
    the already-renamed field names, not the original source names.
    """
    if not value_map:
        return doc
    for field, substitutions in value_map.items():
        if field in doc and str(doc[field]) in substitutions:
            doc[field] = substitutions[str(doc[field])]
    return doc


_CORE_FIELDS = {"id", "title", "content", "url", "source_type", "indexed_at"}


def _doc_from_item(item: dict, fields: dict[str, str], idx: int) -> dict | None:
    """
    Build an ISP ``DocumentItem``-compatible dict from a single API item.

    Uses ``fields`` to locate the id/title/content/url values.  Any item keys
    that are not one of those four source fields are preserved as extra fields
    on the document so extensions and the expand view can access them.

    Returns None if both title and content are empty (nothing meaningful to index).
    The document id is an MD5 hash of the raw id to ensure a stable, URL-safe identifier.
    """
    if not isinstance(item, dict):
        return None
    raw_id = str(item.get(fields["id_field"], idx))
    title = str(item.get(fields["title_field"], ""))
    content = str(item.get(fields["content_field"], ""))
    url = str(item.get(fields["url_field"], ""))
    if not title and not content:
        return None
    doc_id = hashlib.md5(raw_id.encode()).hexdigest()[:16]
    doc: dict = {
        "id": doc_id,
        "title": title or content[:80],
        "content": content or title,
        "url": url,
        "source_type": "rest",
        "indexed_at": int(time.time()),
    }
    # Preserve any extra fields that were mapped (beyond the 4 core shortcuts)
    for k, v in item.items():
        if k not in _CORE_FIELDS and k not in (fields["id_field"], fields["title_field"],
                                                fields["content_field"], fields["url_field"]):
            doc[k] = v
    return doc


async def _do_request(cfg: RestConnectorConfig):
    """
    Execute the configured HTTP request and return the parsed JSON body.

    Sends a browser-like User-Agent header because some APIs reject non-browser
    clients with 403.  JSON body is sent only for POST/PUT/PATCH.
    """
    """Make the HTTP request and return the parsed JSON response."""
    base_headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json, */*",
    }
    merged = {**base_headers, **cfg.headers}
    send_json: dict | None = None
    if cfg.body and cfg.method.upper() in ("POST", "PUT", "PATCH"):
        try:
            send_json = json.loads(cfg.body)
            merged.setdefault("Content-Type", "application/json")
        except json.JSONDecodeError:
            # A configured body that isn't valid JSON is silently dropped —
            # the request goes out bodyless with no trace of why, and the
            # symptom only shows up later as a confusing empty/wrong API response.
            log.warning("_do_request: configured body is not valid JSON, sending request "
                       "without a body: url=%s method=%s", cfg.url, cfg.method, exc_info=True)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.request(cfg.method, cfg.url, headers=merged, json=send_json)
        resp.raise_for_status()
        return resp.json()


async def fetch_raw_items(cfg: RestConnectorConfig, pre_processor_exts: list[dict] | None = None) -> list[dict]:
    """Fetch and return the raw API items without any field-name mapping."""
    from isp.services.infra.extension_runner import run_pre_processor, apply_pre_processor_extensions
    data = await _do_request(cfg)
    if cfg.pre_processor:
        data = await run_pre_processor(cfg.pre_processor, data)
    if pre_processor_exts:
        data = await apply_pre_processor_extensions(data, pre_processor_exts)
    if cfg.result_path:
        items = _extract_path(data, cfg.result_path)
    else:
        _, items = _auto_detect_path(data)
    return [item for item in items if isinstance(item, dict)]


async def fetch_and_parse(cfg: RestConnectorConfig, pre_processor_exts: list[dict] | None = None) -> list[dict]:
    """
    Fetch from the REST API and return ISP-normalised document dicts.

    Steps:
    1. Fetch and extract the item array (via ``result_path`` or auto-detect).
    2. Apply ``field_map`` renames from the connector config.
    3. Detect or use explicit id/title/content/url field names.
    4. Build ``DocumentItem``-compatible dicts, preserving unmapped extra fields.
    """
    from isp.services.infra.extension_runner import run_pre_processor, apply_pre_processor_extensions
    data = await _do_request(cfg)
    if cfg.pre_processor:
        data = await run_pre_processor(cfg.pre_processor, data)
    if pre_processor_exts:
        data = await apply_pre_processor_extensions(data, pre_processor_exts)

    # Auto-detect result path when not explicitly set
    if cfg.result_path:
        items = _extract_path(data, cfg.result_path)
    else:
        _, items = _auto_detect_path(data)

    if not items:
        return []

    # Apply field_map renames to every item first
    if cfg.field_map:
        items = [_apply_field_map(item, cfg.field_map) if isinstance(item, dict) else item
                 for item in items]

    # Apply value_map substitutions on the already-renamed field names.
    # Runs after field_map so outer keys refer to the post-rename field names,
    # matching the documented apply order (field_map → value_map → ISP extraction).
    if cfg.value_map:
        items = [_apply_value_map(item, cfg.value_map) if isinstance(item, dict) else item
                 for item in items]

    # Auto-detect field mapping when defaults haven't been overridden
    sample = next((i for i in items if isinstance(i, dict)), None)
    if sample:
        any_default_missing = any(
            cfg.__dict__.get(f"{f}_field", f) == f and f not in sample
            for f in ("id", "title", "content", "url")
        )
        if any_default_missing:
            fields = _auto_detect_fields(sample)
        else:
            fields = {
                "id_field":      cfg.id_field,
                "title_field":   cfg.title_field,
                "content_field": cfg.content_field,
                "url_field":     cfg.url_field,
            }
    else:
        fields = {"id_field": "id", "title_field": "title", "content_field": "content", "url_field": "url"}

    docs = []
    for i, item in enumerate(items):
        doc = _doc_from_item(item, fields, i)
        if doc:
            docs.append(doc)
    return docs
