# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""Two-product comparisons: independent retrieval and source-only tables."""
from __future__ import annotations
import asyncio
import re
from isp.services.domain.reasoning_data import read_reasoning_data, display_fact
from isp.services.infra.chat import retrieval, query_filters, concept_resolution


def parse_comparison(query: str) -> tuple[str, str] | None:
    text = query.strip().rstrip('?.!')
    explicit = bool(re.match(r'^compare\s+', text, re.I))
    text = re.sub(r'^compare\s+', '', text, flags=re.I)
    parts = re.split(r'\s+(?:vs\.?|versus)\s+', text, flags=re.I)
    if len(parts) == 1 and explicit:
        parts = re.split(r'\s+(?:and|with)\s+', text, flags=re.I)
    if len(parts) != 2 or any(not p.strip() or len(p) > 120 for p in parts):
        return None
    if any(p.strip().lower() in {'this', 'that', 'these', 'those', 'it', 'them'} for p in parts):
        return None
    return parts[0].strip(), parts[1].strip()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r'[a-z0-9]+', text.lower().replace('+', ' plus ')))


def choose_product(query: str, cards: list[dict]) -> dict | None:
    requested = _tokens(query) - {'a', 'an', 'the'}
    variants = {'pro', 'max', 'ultra', 'plus', 'lite', 'fe', 'edge'}
    matches = []
    for card in cards:
        title = _tokens(card.get('title', ''))
        identity = title | _tokens(card.get('manufacturer', ''))
        extra = title - requested - _tokens(card.get('manufacturer', '')) - {'galaxy', '5g'}
        if requested <= identity and not extra and not ((title & variants) - requested):
            matches.append(card)
    return min(matches, key=lambda c: len(_tokens(c.get('title', ''))), default=None)


def _cell(value) -> str:
    text = re.sub(r'<[^>]*>', '', str(value if value not in (None, '') else 'Not listed'))
    return text.replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')


async def retrieve_comparison(sides, index_id, query_by, query_by_weights, facet_query_by,
                              superlative_field_map=None, boost_rules=None):
    async def search(side):
        try:
            price_filter = query_filters.extract_price_filter(side)
            field = await concept_resolution.resolve_concept_field(
                index_id, 'customer_eligibility', superlative_field_map or {},
            )
            if field:
                values = await query_filters.eligibility_values_for_index(index_id, field)
                match = query_filters.match_eligibility_value(side, values) if values else None
                if match:
                    price_filter = query_filters.combine_filters(price_filter, f'{field}:=[`{match[1]}`]')
            cards, _ = await retrieval.search_context(
                side, index_id, query_by, query_by_weights, facet_query_by,
                price_filter=price_filter, boost_rules=boost_rules,
            )
            card = choose_product(side, cards)
            return card, None
        except Exception:
            return None, 'Search temporarily unavailable'

    results = await asyncio.gather(*(search(side) for side in sides))
    cards = []
    for side, (card, _) in zip(sides, results):
        if card:
            cards.append(dict(card, label=side))
    rows = [f'| Feature | {_cell(sides[0])} | {_cell(sides[1])} |', '| --- | --- | --- |']
    def value(card, error, key):
        if card is None:
            return error or 'No exact product match found'
        if key == 'in_stock':
            return {True: 'In stock', False: 'Out of stock'}.get(card.get(key), 'Not listed')
        return card.get(key)
    for label, key in [('Product', 'title'), ('Manufacturer', 'manufacturer'), ('Catalog price', 'price'),
                       ('Availability', 'in_stock'), ('Catalog description', 'summary')]:
        rows.append('| ' + label + ' | ' + ' | '.join(_cell(value(c, e, key)) for c, e in results) + ' |')
    facts_by_side = [{f['key']: f for f in read_reasoning_data(c.get('reasoningData'))['facts']}
                     if c else {} for c, _ in results]
    keys = dict.fromkeys(key for facts in facts_by_side for key in facts)
    for key in keys:
        example = next(facts[key] for facts in facts_by_side if key in facts)
        rows.append('| ' + _cell(example['label']) + ' | ' + ' | '.join(
            _cell(display_fact(facts[key]) if key in facts else None) for facts in facts_by_side) + ' |')
    return cards, '\n'.join(rows) + '\n\nOnly listed catalog data is compared. Prices exclude tax and shipping; missing specifications are not inferred.'
