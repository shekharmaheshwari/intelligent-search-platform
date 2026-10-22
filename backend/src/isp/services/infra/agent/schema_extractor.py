# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Build a schema tree from a list of sample JSON items.

The schema is a flat dict mapping field paths (dot-notation for nested keys)
to inferred types. This gives Claude a concise structural overview without
sending the full data payload in the prompt.
"""
from __future__ import annotations


def _infer_type(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        if not value:
            return "array[]"
        inner = _infer_type(value[0])
        return f"array[{inner}]"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "unknown"


def _flatten(obj: dict, prefix: str = "", depth: int = 0) -> dict[str, str]:
    """Recursively flatten nested dicts up to depth 3."""
    result: dict[str, str] = {}
    if depth > 3:
        return result
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and depth < 3:
            result[key] = "object"
            result.update(_flatten(v, key, depth + 1))
        elif isinstance(v, list) and v and isinstance(v[0], dict) and depth < 2:
            result[key] = f"array[object] ({len(v)} items)"
            # Show schema of first array element
            result.update(_flatten(v[0], f"{key}[]", depth + 1))
        else:
            result[key] = _infer_type(v)
    return result


def extract(sample: list[dict]) -> dict[str, str]:
    """
    Merge field schemas across all sample items.

    Fields present in any item appear in the output; their type is the first
    non-null type seen. This handles sparse optional fields that may be missing
    from some items.
    """
    merged: dict[str, str] = {}
    for item in sample:
        flat = _flatten(item)
        for field, ftype in flat.items():
            if field not in merged or merged[field] == "null":
                merged[field] = ftype
    return merged
