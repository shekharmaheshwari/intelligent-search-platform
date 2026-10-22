# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
JSON file connector: read a local file or remote https:// URL and return ISP documents.

Two public entry points matching the rest_connector API:
- ``fetch_raw_items`` — raw dicts, no field renaming (used when transform extensions are assigned)
- ``fetch_and_parse`` — applies field detection and returns DocumentItem-compatible dicts

The file is expected to be a JSON array or a dict containing one array value.
``root_key`` in the connector config pins the array key; leave empty to auto-detect.
"""
from __future__ import annotations
import hashlib, json, time
from isp.models import JsonFileConnectorConfig


def _load_data(cfg: JsonFileConnectorConfig) -> list[dict]:
    """Load and return the item array from the configured file or URL."""
    if cfg.path.startswith("https://") or cfg.path.startswith("http://"):
        import httpx
        resp = httpx.get(cfg.path, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    else:
        with open(cfg.path, "r", encoding="utf-8") as f:
            data = json.load(f)

    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]

    if isinstance(data, dict):
        if cfg.root_key and cfg.root_key in data:
            raw = data[cfg.root_key]
        else:
            # auto-detect: first non-empty list value
            raw = next((v for v in data.values() if isinstance(v, list) and v), [])
        return [i for i in raw if isinstance(i, dict)]

    return []


def _doc_from_item(item: dict, cfg: JsonFileConnectorConfig, idx: int) -> dict | None:
    raw_id  = str(item.get(cfg.id_field, idx))
    title   = str(item.get(cfg.title_field, ""))
    content = str(item.get(cfg.content_field, ""))
    url     = str(item.get(cfg.url_field, ""))
    if not title and not content:
        return None
    doc_id = hashlib.md5(raw_id.encode()).hexdigest()[:16]
    doc: dict = {
        "id":          doc_id,
        "title":       title or content[:80],
        "content":     content or title,
        "url":         url,
        "source_type": "json_file",
        "indexed_at":  int(time.time()),
    }
    skip = {cfg.id_field, cfg.title_field, cfg.content_field, cfg.url_field,
            "id", "title", "content", "url", "source_type", "indexed_at"}
    for k, v in item.items():
        if k not in skip:
            doc[k] = v
    return doc


async def fetch_raw_items(cfg: JsonFileConnectorConfig) -> list[dict]:
    """Return the raw item dicts without any ISP field mapping."""
    return _load_data(cfg)


async def fetch_and_parse(cfg: JsonFileConnectorConfig) -> list[dict]:
    """Return ISP-normalised document dicts from the JSON file."""
    items = _load_data(cfg)
    docs = []
    for i, item in enumerate(items):
        doc = _doc_from_item(item, cfg, i)
        if doc:
            docs.append(doc)
    return docs
