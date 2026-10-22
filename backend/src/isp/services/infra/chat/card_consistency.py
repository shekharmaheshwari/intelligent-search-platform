# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Card consistency — keeping the product carousel in sync with what the LLM's
finished answer actually talks about.
"""
from __future__ import annotations
import re

_TRAILING_LINE_COUNT_RE = re.compile(r'\s*\(\d+(?:[–-]\d+)?\s+lines?\)\s*$', re.IGNORECASE)


def _matchable_title(title: str) -> str:
    """
    Strip a trailing "(N lines)"/"(N–M lines)" annotation before matching a
    card's title against the LLM's answer text. plans_transformer.js appends
    that range to the CARD's title so the carousel shows it, but a natural-
    language answer never reproduces it verbatim ("...is the Essentials 4
    Line Offer 2.0, which costs $125/mo for 5 lines" — not "...Offer 2.0 (4–6
    lines), which..."). Reproduced live: every plans card's full title failed
    to match for every query tried, so filter_cards_by_response's
    "no match found" fallback fired every time, silently showing all 5
    unfiltered candidates regardless of which one the model actually
    discussed. A no-op for any other index — no other title has this shape.
    """
    return _TRAILING_LINE_COUNT_RE.sub('', title)


def filter_cards_by_response(cards: list[dict], response_text: str) -> list[dict]:
    """
    Keep only cards whose title is actually mentioned in the LLM's finished answer.

    Plain substring containment would let a shorter title falsely match inside a
    longer, different one (e.g. "Pixel 9" is a substring of "Pixel 9a"; "Galaxy S26"
    of "Galaxy S26+"). \\b can't be used for this: it anchors on a \\w/\\W transition,
    which breaks down whenever a title ends in punctuation ("Galaxy S26+") — the
    character after "+" is a space, both non-word, so \\b never matches there and
    the title fails to match even its own literal, correct mention. Anchoring on
    "not adjacent to an alphanumeric character" instead (regardless of what
    character sits at the title's own edge) fixes that self-match, but titles can
    still contain one another as substrings ("Galaxy S26" inside "Galaxy S26+").
    So titles are checked longest-first, and each match claims its text span —
    a shorter title only counts if it has a real standalone occurrence outside any
    span a longer title already claimed. If nothing matches (e.g. the model
    paraphrased every name, or named no product at all), fall back to the original
    unfiltered list rather than showing an empty carousel.

    That fallback runs independently PER multi-intent bucket, not once over the
    whole list. multi_intent.try_multi_intent_context stamps card["label"] =
    plan.label on every card (cards outside multi-intent have no "label" key —
    they all share the one None group here, which reproduces the original
    single-list behavior exactly). Filtering the merged list as one unit let a
    match in one bucket license dropping an entire OTHER bucket: the answer
    might name one headphones product by exact title while only describing the
    case results in prose, and the old single "any match at all -> don't fall
    back" rule then discarded every case card even though the case section of
    the answer was perfectly legitimate, just not verbatim.
    """
    if not response_text:
        return cards
    low = response_text.lower()

    groups: dict[object, list[dict]] = {}
    order: list[object] = []
    for c in cards:
        key = c.get("label")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    result: list[dict] = []
    for key in order:
        group = groups[key]
        claimed = bytearray(len(low))
        used_ids: set[int] = set()
        for c in sorted(group, key=lambda c: -len(_matchable_title(c.get("title") or ""))):
            title = _matchable_title(c.get("title") or "")
            if not title:
                continue
            pattern = r'(?<![A-Za-z0-9])' + re.escape(title.lower()) + r'(?![A-Za-z0-9])'
            for m in re.finditer(pattern, low):
                if not any(claimed[m.start():m.end()]):
                    used_ids.add(id(c))
                    claimed[m.start():m.end()] = b'\x01' * (m.end() - m.start())
                    break
        used = [c for c in group if id(c) in used_ids]
        result.extend(used or group)
    return result
