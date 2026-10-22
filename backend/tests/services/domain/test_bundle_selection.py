# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

from decimal import Decimal
import pytest
from isp.services.domain.bundle_selection import Budget, extract_budget, select_bundles, render_bundles


def card(id, price):
    return {'id': id, 'title': id, 'bundle_price': price}


@pytest.mark.parametrize('phrase,inclusive', [('under', False), ('below', False), ('up to', True), ('at most', True)])
def test_extract_total_leaves_product_query(phrase, inclusive):
    query, budget = extract_budget(f'An iPhone 17 case and screen protector for {phrase} $50 total')
    assert query == 'An iPhone 17 case and screen protector'
    assert budget == Budget(Decimal('50'), inclusive)


def test_per_item_budget_is_not_a_bundle():
    assert extract_budget('case and protector each under $50')[1] is None


def test_exact_total_and_decimal_precision():
    groups = [[card('case', '19.99')], [card('protector', '30.01')]]
    assert select_bundles(groups, Budget(Decimal('50'), False)) == []
    bundles = select_bundles(groups, Budget(Decimal('50'), True))
    assert bundles[0][1] == Decimal('50.00')


def test_cheapest_complete_pairs_and_independent_labels():
    groups = [[card('case', '20')], [card('p1', '10'), card('p2', '15')]]
    cards, message = render_bundles(groups, Budget(Decimal('50'), False))
    assert len(cards) == 4
    assert [c['bundle_total'] for c in cards] == ['30.00', '30.00', '35.00', '35.00']
    assert cards[0]['label'] != cards[2]['label']
    assert 'label' not in groups[0][0]
    assert 'before tax and shipping' in message


@pytest.mark.parametrize('price', [None, True, 'NaN', 'Infinity', '-1', '$5/month'])
def test_unusable_prices_never_qualify(price):
    assert select_bundles([[card('a', price)], [card('b', '10')]], Budget(Decimal('50'), False)) == []


def test_duplicate_product_cannot_satisfy_two_intents():
    assert select_bundles([[card('same', 5)], [card('same', 5)]], Budget(Decimal('50'), False)) == []


def test_empty_group_never_returns_partial_bundle():
    cards, message = render_bundles([[card('a', 5)], []], Budget(Decimal('50'), False))
    assert cards == []
    assert 'No qualifying bundle' in message
