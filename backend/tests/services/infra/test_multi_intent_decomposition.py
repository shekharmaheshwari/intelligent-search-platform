from unittest.mock import AsyncMock

from isp.services.infra.chat import multi_intent


async def test_same_category_retrieval_keeps_labels_prices_and_sorting(monkeypatch):
    monkeypatch.setattr(multi_intent, '_category_vectors_for_index', AsyncMock(return_value=(
        {'cases': [1.0, 0.0]}, {'cases': 'Cases'},
    )))
    resolve_device = AsyncMock(side_effect=lambda q, *args:
        'devices:=[`iPhone 17`]' if 'iPhone 17' in q else 'devices:=[`Galaxy S25`]')
    monkeypatch.setattr(multi_intent, 'resolve_field_value_filter', resolve_device)
    monkeypatch.setattr(multi_intent, '_is_device_fit_category', AsyncMock(return_value=True))
    monkeypatch.setattr(multi_intent, 'resolve_concept_field', AsyncMock(return_value='price'))
    sorted_search = AsyncMock(side_effect=[([{'id': '1'}], 'first'), ([{'id': '2'}], 'second')])
    monkeypatch.setattr(multi_intent.retrieval, 'search_context_sorted', sorted_search)
    plain_search = AsyncMock()
    monkeypatch.setattr(multi_intent.retrieval, 'search_context', plain_search)
    segments = ['cheapest iPhone 17 case under $25', 'cheapest Galaxy S25 case under $40']
    result = await multi_intent.try_multi_intent_context(
        ' and '.join(segments), 'index', 'title', None, None,
        'eligible:=true', None, 'devices',
        embed=AsyncMock(return_value=[1.0, 0.0]), confirm=None,
        generic_category_values=set(), multi_intent_split_enabled=True,
    )
    assert result is not None
    cards, context = result
    assert len(cards) == 2 and cards[0]['label'] != cards[1]['label']
    assert 'first' in context and 'second' in context
    plain_search.assert_not_called()
    calls = sorted_search.call_args_list
    assert len(calls) == 2
    assert calls[0].args[2:4] == ('price', 'asc')
    assert 'iPhone 17' in calls[0].kwargs['price_filter']
    assert 'Galaxy S25' not in calls[0].kwargs['price_filter']
    assert 'Galaxy S25' in calls[1].kwargs['price_filter']
    assert '25' in calls[0].kwargs['price_filter']
    assert '40' in calls[1].kwargs['price_filter']
    assert all('eligible:=true' in c.kwargs['price_filter'] for c in calls)


async def test_disabled_feature_does_not_call_decomposer(monkeypatch):
    from unittest.mock import Mock
    decompose = Mock()
    monkeypatch.setattr(multi_intent.query_decomposition, "decompose_query", decompose)
    assert await multi_intent.try_multi_intent_context(
        'case and headphones', 'index', 'title', None, None, None, None, None,
        embed=AsyncMock(), confirm=None, generic_category_values=set(),
        multi_intent_split_enabled=False,
    ) is None
    decompose.assert_not_called()


async def test_sentence_query_retrieves_both_categories_without_llm(monkeypatch):
    monkeypatch.setattr(multi_intent, '_category_vectors_for_index', AsyncMock(return_value=(
        {'cases': [1.0, 0.0], 'headphones': [0.0, 1.0]},
        {'cases': 'Cases', 'headphones': 'Headphones'},
    )))
    search = AsyncMock(side_effect=[([{'id': 'case'}], 'case context'),
                                    ([{'id': 'headphones'}], 'headphones context')])
    monkeypatch.setattr(multi_intent.retrieval, 'search_context', search)
    result = await multi_intent.try_multi_intent_context(
        'A waterproof case for my iPhone 17. Also need wireless headphones for running.',
        'index', 'title', None, None, None, None, None,
        embed=AsyncMock(side_effect=[[1.0, 0.0], [0.0, 1.0]]), confirm=None,
        generic_category_values=set(), multi_intent_split_enabled=True,
    )
    assert result is not None
    cards, _ = result
    assert [c['label'] for c in cards] == ['Cases', 'Headphones']
    assert search.call_count == 2
    assert search.call_args_list[1].args[0] == 'wireless headphones for running'
    assert search.call_args_list[1].kwargs['price_filter'] == 'category:=[headphones]'


async def test_total_budget_selects_complete_pair(monkeypatch):
    monkeypatch.setattr(multi_intent, '_category_vectors_for_index', AsyncMock(return_value=(
        {'cases': [1., 0.], 'screen-protectors': [0., 1.]},
        {'cases': 'Cases', 'screen-protectors': 'Screen Protectors'},
    )))
    async def resolve_device(query, *args):
        # The live catalog lookup fails with the leading article intact.
        if query == 'iPhone 17 case':
            return 'devices:=[`iPhone 17`]'
        return None
    monkeypatch.setattr(multi_intent, 'resolve_field_value_filter', resolve_device)
    monkeypatch.setattr(multi_intent, '_is_device_fit_category', AsyncMock(return_value=True))
    sorted_search = AsyncMock(side_effect=[
        ([{'id': 'case', 'title': 'Case', 'bundle_price': 29.99}], ''),
        ([{'id': 'protector', 'title': 'Protector', 'bundle_price': 15},
          {'id': 'expensive', 'title': 'Expensive', 'bundle_price': 25}], ''),
    ])
    monkeypatch.setattr(multi_intent.retrieval, 'search_context_sorted', sorted_search)
    result = await multi_intent.try_multi_intent_context(
        'An iPhone 17 case and screen protector for under $50 total',
        'index', 'title', None, None, None, None, 'devices',
        embed=AsyncMock(side_effect=AssertionError('No embedding required')), confirm=None,
        generic_category_values=set(), multi_intent_split_enabled=True,
    )
    cards, context = result
    assert [c['id'] for c in cards] == ['case', 'protector']
    assert all(c['bundle_total'] == '44.99' for c in cards)
    assert context.startswith('Bundle selection: ')
    assert sorted_search.call_count == 2
    assert all('price:<50' in c.kwargs['price_filter'] for c in sorted_search.call_args_list)
    assert all('total' not in c.args[0] for c in sorted_search.call_args_list)

    assert all('devices:=[`iPhone 17`]' in c.kwargs['price_filter'] for c in sorted_search.call_args_list)
