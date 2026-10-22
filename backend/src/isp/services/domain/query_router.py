# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""Conservative local routing; uncertain requests retain the configured LLM path."""
import re
from isp.services.domain.query_decomposition import _pipeline, _matcher

_REASONING = re.compile(
    r'\b(?:why|how|explain|compare|versus|vs|recommend|better|best|should|worth|'
    r'difference|differences|which|advice|suitable|then|total|combined|altogether)\b|'
    r'^\s*(?:is|are|can|could|would|does|do|will|what|when|where)\b|\?', re.I,
)


def can_answer_locally(query: str, messages: list[dict], labels: list[str], *, bundles_enabled: bool) -> bool:
    """Only standalone catalog searches and explicitly supported total budgets."""
    from isp.services.domain.bundle_selection import extract_budget

    if messages or not query.strip() or len(query) > 4000:
        return False
    body, budget = extract_budget(query)
    if budget and not bundles_enabled:
        return False
    if _REASONING.search(body):
        return False
    try:
        return bool(_matcher(tuple(sorted(set(labels))))(_pipeline().make_doc(body)))
    except (ImportError, OSError):
        return False
