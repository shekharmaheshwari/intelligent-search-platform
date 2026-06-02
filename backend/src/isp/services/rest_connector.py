from __future__ import annotations
import hashlib, json, time
import httpx
from isp.models import RestConnectorConfig

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
    if not path:
        return data if isinstance(data, list) else [data]
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part, [])
        elif isinstance(data, list):
            data = [item.get(part) for item in data if isinstance(item, dict)]
        else:
            return []
    return data if isinstance(data, list) else [data]


def _doc_from_item(item: dict, fields: dict[str, str], idx: int) -> dict | None:
    if not isinstance(item, dict):
        return None
    raw_id = str(item.get(fields["id_field"], idx))
    title = str(item.get(fields["title_field"], ""))
    content = str(item.get(fields["content_field"], ""))
    url = str(item.get(fields["url_field"], ""))
    if not title and not content:
        return None
    doc_id = hashlib.md5(raw_id.encode()).hexdigest()[:16]
    return {
        "id": doc_id,
        "title": title or content[:80],
        "content": content or title,
        "url": url,
        "source_type": "rest",
        "indexed_at": int(time.time()),
    }


async def _do_request(cfg: RestConnectorConfig):
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
            pass
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.request(cfg.method, cfg.url, headers=merged, json=send_json)
        resp.raise_for_status()
        return resp.json()


async def fetch_raw_items(cfg: RestConnectorConfig) -> list[dict]:
    """Fetch and return the raw API items without any field-name mapping."""
    data = await _do_request(cfg)
    if cfg.result_path:
        items = _extract_path(data, cfg.result_path)
    else:
        _, items = _auto_detect_path(data)
    return [item for item in items if isinstance(item, dict)]


async def fetch_and_parse(cfg: RestConnectorConfig) -> list[dict]:
    data = await _do_request(cfg)

    # Auto-detect result path when not explicitly set
    if cfg.result_path:
        items = _extract_path(data, cfg.result_path)
    else:
        _, items = _auto_detect_path(data)

    if not items:
        return []

    # Auto-detect field mapping when defaults haven't been overridden
    defaults = {"id", "title", "content", "url"}
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
