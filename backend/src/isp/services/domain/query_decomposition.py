# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""Local, rule-based spaCy decomposition using the catalog's own terminology.

No model download, external API, or generated query words. Unknown vocabulary
falls back to the existing embedding classifier rather than guessed categories.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from isp.services.domain.chat_intent_split import MAX_INTENTS, _clean_segment, split_segments

log = logging.getLogger(__name__)
_UNSUPPORTED = re.compile(r'\b(?:or|versus|vs|compare|then|combined|altogether|total)\b', re.I)
_EACH = re.compile(r',?\s+each\s+((?:under|below|over|above)\s+\$?\d+(?:\.\d+)?)\s*[.!?]?$', re.I)


@lru_cache(maxsize=1)
def _pipeline():
    import spacy

    nlp = spacy.blank('en')
    nlp.add_pipe('sentencizer')
    return nlp


@lru_cache(maxsize=32)
def _matcher(labels: tuple[str, ...]):
    from spacy.matcher import PhraseMatcher

    nlp = _pipeline()
    matcher = PhraseMatcher(nlp.vocab, attr='LOWER')
    terms = set()
    for label in labels:
        # Facets such as cases-&-covers provide two catalog-backed aliases.
        for term in re.split(r'\s+(?:and|&)\s+', label.replace('-', ' ').lower()):
            term = term.strip()
            if not term:
                continue
            terms.add(term)
            # Conservative regular plural support; not general lemmatization.
            if term.endswith('s') and not term.endswith(('ss', 'us', 'is')):
                terms.add(term[:-1])
    matcher.add('PRODUCT', [nlp.make_doc(term) for term in sorted(terms)])
    return matcher


def decompose_query(query: str, category_labels: list[str]) -> tuple[str, list[str]]:
    """Return split/single/unsupported/fallback and source-based search queries."""
    if len(query) > 4000 or not query.strip():
        return 'unsupported', []
    if _UNSUPPORTED.search(query):
        return 'unsupported', []
    try:
        nlp = _pipeline()
        matcher = _matcher(tuple(sorted(set(category_labels))))
        shared_price = _EACH.search(query)
        body = query[:shared_price.start()] if shared_price else query
        segments = []
        for sentence in nlp(body).sents:
            text = _clean_segment(sentence.text)
            parts = split_segments(text)
            # Split conjunctions only when every part names a catalog product.
            # "waterproof and durable case" stays intact, retaining modifiers.
            if parts and all(matcher(nlp.make_doc(part)) for part in parts):
                segments.extend(parts)
            else:
                segments.append(text)
        segments = [s for s in segments if s]
        if len(segments) > MAX_INTENTS:
            return 'unsupported', []
        if not all(matcher(nlp.make_doc(s)) for s in segments):
            return 'fallback', []
        if len(segments) < 2:
            # Multiple product mentions that the conservative rules couldn't
            # separate should still be handled by the legacy classifier.
            return ('fallback' if split_segments(body) else 'single'), []
        if shared_price:
            segments = [f'{s} {shared_price.group(1)}' for s in segments]
        return 'split', segments
    except (ImportError, OSError):
        log.warning('Local spaCy unavailable; using legacy query splitting', exc_info=True)
        return 'fallback', []


def match_categories(segments: list[str], labels: dict[str, str]) -> dict[int, str]:
    """Trust only a unique catalog category; ambiguous/unknown spans use embeddings."""
    try:
        nlp = _pipeline()
        result = {}
        for i, segment in enumerate(segments):
            doc = nlp.make_doc(segment)
            matches = [category for category, label in labels.items()
                       if _matcher((label,))(doc)]
            if len(matches) == 1:
                result[i] = matches[0]
        return result
    except (ImportError, OSError):
        return {}
