# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Viewed-item and purchased-item scoring policy for personalised search.

The policy is stored per-index as a nested dict and editable from the
Personalisation tab without triggering a rebuild.

Policy shape:
  {
    "viewed":    { "mode": "penalise"|"neutral"|"boost", "value": float },
    "purchased": { "mode": "penalise"|"neutral"|"boost", "value": float },
  }

Research basis (Hu et al. 2008; Linden et al. 2003; Covington et al. 2016):
- Viewed but not purchased → active interest, need unfulfilled → boost or neutral
- Purchased (durable good)  → need satisfied            → penalise (exclude from next results)
"""
from __future__ import annotations

_DEFAULT_POLICY: dict = {
    "viewed":    {"mode": "penalise", "value": 0.05},
    "purchased": {"mode": "penalise", "value": 0.30},
}


def migrate(raw: dict | None) -> dict:
    """
    Upgrade a policy dict to the current nested format.

    Handles the legacy flat format ``{"mode": ..., "value": ...}`` written
    before the viewed/purchased split, promoting it to the nested shape while
    preserving the original mode/value for the ``viewed`` sub-policy.
    """
    if not raw:
        return {k: dict(v) for k, v in _DEFAULT_POLICY.items()}
    if "mode" in raw:
        return {
            "viewed":    {"mode": raw.get("mode", "penalise"), "value": float(raw.get("value", 0.05))},
            "purchased": dict(_DEFAULT_POLICY["purchased"]),
        }
    return raw


def _score(sub: dict) -> float:
    mode  = sub.get("mode", "penalise")
    value = float(sub.get("value", 0.05))
    if mode == "boost":
        return value
    if mode == "penalise":
        return -value
    return 0.0


def apply(
    doc_id:       str,
    viewed_ids:   set[str],
    purchased_ids: set[str],
    policy:       dict | None,
) -> float:
    """
    Return the additive score adjustment for a single document.

    Purchase signal takes precedence over view: if an item was both viewed
    and purchased, the purchased sub-policy applies.

    Returns 0.0 when the doc was not interacted with or the relevant mode
    is ``neutral``.
    """
    p = migrate(policy)
    if doc_id in purchased_ids:
        return _score(p.get("purchased", _DEFAULT_POLICY["purchased"]))
    if doc_id in viewed_ids:
        return _score(p.get("viewed", _DEFAULT_POLICY["viewed"]))
    return 0.0
