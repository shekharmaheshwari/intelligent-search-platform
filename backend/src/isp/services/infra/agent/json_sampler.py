# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Load a JSON data file (local path or https:// URL), detect the root array key,
and return a small sample for schema analysis.
"""
from __future__ import annotations
import json


def _extract_items(data: object, root_key: str = "") -> tuple[str, list[dict]]:
    """Return (detected_root_key, items)."""
    if isinstance(data, list):
        items = [i for i in data if isinstance(i, dict)]
        return ("", items)

    if isinstance(data, dict):
        if root_key and root_key in data:
            raw = data[root_key]
            if isinstance(raw, list):
                return (root_key, [i for i in raw if isinstance(i, dict)])

        # Auto-detect: first non-empty list of dicts
        for k, v in data.items():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return (k, [i for i in v if isinstance(i, dict)])

    return ("", [])


async def load(path: str, sample_size: int = 5, root_key: str = "") -> tuple[list[dict], str, int]:
    """
    Load a JSON file or URL and return (sample, detected_root_key, total_count).

    ``sample`` contains up to ``sample_size`` representative items spread
    evenly through the full array so Claude sees variety across large catalogs.
    """
    # Strip file:// or file:/// browser-dragged prefixes → convert to bare path
    if path.startswith("file:///"):
        path = path[7:]   # keep leading /
    elif path.startswith("file://"):
        path = path[7:]

    if path.startswith("http://") or path.startswith("https://"):
        import httpx
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(path)
            resp.raise_for_status()
            data = resp.json()
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

    detected_key, items = _extract_items(data, root_key)
    total = len(items)

    if total <= sample_size:
        sample = items
    else:
        # Evenly spaced sample: first, last, and distributed middle items
        step = total // (sample_size - 1)
        indices = sorted({0, total - 1} | {i * step for i in range(1, sample_size - 1)})
        sample = [items[i] for i in indices[:sample_size]]

    return sample, detected_key, total
