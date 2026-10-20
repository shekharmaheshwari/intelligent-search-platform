from unittest.mock import Mock
import pytest
from isp.services.infra.chat import query_filters, retrieval


@pytest.mark.parametrize('field, expected', [
    ('eligible_lines', ['lines', 'line']),
    ('guest_counts', ['guests', 'guest']),
    ('seats', ['seats', 'seat']),
])
def test_unit_terms_from_field(field, expected):
    assert query_filters.unit_terms_from_field(field) == expected


@pytest.mark.parametrize('query, expected', [
    ('cheapest plan for single line', ('single line', 1)),
    ('cheapest plan for 3 lines', ('3 lines', 3)),
    ('best plan for two lines', ('two lines', 2)),
    ('plan for a line', ('a line', 1)),
    ('plan for my family of 4', ('family of 4', 4)),
])
def test_extract_unit_count(query, expected):
    assert query_filters.extract_unit_count(query, ['lines', 'line']) == expected


@pytest.mark.parametrize('query', ['iphone 15 case', 'unlimited plan', 'plan with 50 gb', '0 lines'])
def test_extract_unit_count_ignores_non_quantities(query):
    assert query_filters.extract_unit_count(query, ['lines', 'line']) is None


def _hit(id_, title, lines, prices):
    return {'document': {'id': id_, 'title': title, 'eligible_lines': lines, 'line_prices': prices}}


@pytest.mark.parametrize('direction, first', [('asc', 'Essentials Saver'), ('desc', 'Experience Beyond')])
async def test_search_context_at_quantity_ranks_by_price_at_n(monkeypatch, direction, first):
    hits = [
        _hit('beyond', 'Experience Beyond', [1, 2, 3], [100, 170, 170]),
        _hit('saver', 'Essentials Saver', [1, 2], [50, 80]),
        _hit('ess', 'Essentials', [1, 2, 3], [60, 90, 90]),
        _hit('broken', 'Broken arrays', [1, 2], [10]),          # misaligned -> dropped
    ]
    client = Mock()
    client.multi_search.perform.return_value = {'results': [{'hits': hits}]}
    monkeypatch.setattr(retrieval.ts, 'get_client', lambda: client)
    monkeypatch.setattr(retrieval.ts, 'collection_name', lambda i: f'isp_{i}')

    cards, context = await retrieval.search_context_at_quantity(
        'plan', 'idx', 'eligible_lines', 'line_prices', 1, direction,
        base_filter='eligible_lines:=1', superlative_phrase='cheapest',
    )
    assert cards[0]['title'] == first
    assert [c['id'] for c in cards if c['id'] == 'broken'] == []
    assert 'Price at quantity 1 (eligible_lines=1): ' in context
    search = client.multi_search.perform.call_args[0][0]['searches'][0]
    assert search['filter_by'] == 'eligible_lines:=1'
    assert search['q'] == '*'          # leftover text never narrows the candidate set
    assert 'sort_by' not in search


def test_extract_unit_counts_returns_every_quantity_in_order():
    q = 'cheapest plan for 2 lines and cheapest plan for family of 4'
    assert query_filters.extract_unit_counts(q, ['lines', 'line']) == [('2 lines', 2), ('family of 4', 4)]


async def test_search_context_at_quantity_pages_through_full_set(monkeypatch):
    # 300 candidates over two pages; the true cheapest is on page 2.
    all_hits = [_hit(f'p{i}', f'Plan {i}', [3], [500 - i]) for i in range(300)]
    pages = {'1': all_hits[:250], '2': all_hits[250:]}
    client = Mock()
    client.multi_search.perform.side_effect = lambda body, _: {'results': [
        {'hits': pages[body['searches'][0]['page']], 'found': 300}]}
    monkeypatch.setattr(retrieval.ts, 'get_client', lambda: client)
    monkeypatch.setattr(retrieval.ts, 'collection_name', lambda i: f'isp_{i}')
    cards, _ = await retrieval.search_context_at_quantity(
        'plan', 'idx', 'eligible_lines', 'line_prices', 3, 'asc', base_filter='eligible_lines:=3')
    assert cards[0]['title'] == 'Plan 299'
    assert client.multi_search.perform.call_count == 2


def _constraint_client(facets: dict, titles: dict, content_words: set = frozenset()):
    """Fake Typesense: facet_query -> `facets`; title search -> `titles` {word: [(id, title)]};
    content search (per_page 0, no facet) -> found if word in `content_words`."""
    def perform(body, _):
        out = []
        for s in body['searches']:
            if 'facet_query' in s:
                word = s['facet_query'].split(':', 1)[1]
                out.append({'facet_counts': [{'field_name': s['facet_by'], 'counts': [
                    {'value': v} for v in facets.get(word, [])]}]})
            elif s.get('per_page') == '0':
                out.append({'found': 1 if s['q'] in content_words else 0, 'hits': []})
            else:
                docs = titles.get(s['q'], [])
                out.append({'found': len(docs), 'hits': [{'document': {'id': i, 'title': t}} for i, t in docs]})
        return {'results': out}
    client = Mock()
    client.multi_search.perform.side_effect = perform
    return client


async def test_text_constraints_features_titles_negation_and_unapplied(monkeypatch):
    client = _constraint_client(
        facets={'netflix': ['Netflix Standard with ads ON US'], 'hulu': ['Hulu ON US']},
        titles={'essentials': [('c26l1e', 'Essentials 2.0'), ('c26l1es', 'Essentials Saver 2.0')]},
        content_words={'plan'},
    )
    monkeypatch.setattr(query_filters.ts, 'get_client', lambda: client)
    monkeypatch.setattr(query_filters.ts, 'collection_name', lambda i: f'isp_{i}')
    filters, unapplied = await query_filters.text_constraint_filters(
        'essentials plan with netflix without hulu from verizon', 'idx', 'highlight_features',
        content_query_by='chatContent,content')
    assert 'highlight_features:=[`Netflix Standard with ads ON US`]' in filters
    assert 'highlight_features:!=[`Hulu ON US`]' in filters            # "without" excludes
    assert 'id:=[`c26l1e`,`c26l1es`]' in filters                       # named item via title
    assert unapplied == ['verizon']      # unknown term reported; "plan" is in product text
    assert not any('plan' in f or 'verizon' in f for f in filters)


async def test_text_constraints_negated_title_uses_exact_title_exclusion(monkeypatch):
    # Typesense 0.25 rejects `id:!=`, so exclusion must go through exact titles.
    client = _constraint_client(facets={}, titles={'beyond': [('c26l1t1', 'Experience Beyond 2.0')]})
    monkeypatch.setattr(query_filters.ts, 'get_client', lambda: client)
    monkeypatch.setattr(query_filters.ts, 'collection_name', lambda i: f'isp_{i}')
    filters, unapplied = await query_filters.text_constraint_filters('not beyond', 'idx', None)
    assert filters == ['title:!=[`Experience Beyond 2.0`]'] and unapplied == []


async def test_text_constraints_no_words_no_query(monkeypatch):
    client = Mock()
    monkeypatch.setattr(query_filters.ts, 'get_client', lambda: client)
    assert await query_filters.text_constraint_filters('for the', 'idx', 'f') == ([], [])
    client.multi_search.perform.assert_not_called()


async def test_failed_search_is_logged_not_silent(monkeypatch, caplog):
    client = Mock()
    client.multi_search.perform.return_value = {'results': [{'code': 400, 'error': 'bad filter'}]}
    monkeypatch.setattr(retrieval.ts, 'get_client', lambda: client)
    monkeypatch.setattr(retrieval.ts, 'collection_name', lambda i: f'isp_{i}')
    with caplog.at_level('WARNING'):
        cards, _ = await retrieval.search_context_at_quantity(
            'plan', 'idx', 'eligible_lines', 'line_prices', 1, 'asc', base_filter='x:=1')
    assert cards == [] and 'bad filter' in caplog.text


async def test_unapplied_terms_are_stated_in_context(monkeypatch):
    client = Mock()
    client.multi_search.perform.return_value = {'results': [{'hits': [_hit('s', 'Saver', [1], [50])]}]}
    monkeypatch.setattr(retrieval.ts, 'get_client', lambda: client)
    monkeypatch.setattr(retrieval.ts, 'collection_name', lambda i: f'isp_{i}')
    _, context = await retrieval.search_context_at_quantity(
        'plan', 'idx', 'eligible_lines', 'line_prices', 1, 'asc', unapplied_terms=['5g'])
    assert context.startswith('Note: ') and '5g' in context


async def test_multi_intent_applies_each_segments_own_quantity(monkeypatch):
    from unittest.mock import AsyncMock
    from isp.services.infra.chat import multi_intent
    monkeypatch.setattr(multi_intent, '_category_vectors_for_index', AsyncMock(return_value=(
        {'plans': [1.0, 0.0]}, {'plans': 'Plans'})))
    monkeypatch.setattr(multi_intent, 'resolve_concept_field', AsyncMock(return_value='price'))
    monkeypatch.setattr(multi_intent, 'text_constraint_filters', AsyncMock(return_value=([], [])))
    qty = AsyncMock(side_effect=[([{'id': 'a'}], 'two'), ([{'id': 'b'}], 'four')])
    monkeypatch.setattr(multi_intent.retrieval, 'search_context_at_quantity', qty)
    sorted_search = AsyncMock()
    monkeypatch.setattr(multi_intent.retrieval, 'search_context_sorted', sorted_search)
    result = await multi_intent.try_multi_intent_context(
        'cheapest plan for 2 lines and cheapest plan for 4 lines', 'index', 'title', None, None,
        'customer_group:=[`consumer`]', None, None,
        embed=AsyncMock(return_value=[1.0, 0.0]), confirm=None,
        generic_category_values=set(), multi_intent_split_enabled=True,
        unit_count_field='eligible_lines', unit_price_field='line_prices',
        feature_field='highlight_features',
    )
    assert result is not None
    sorted_search.assert_not_called()                       # never ranks by scalar price
    calls = qty.call_args_list
    assert sorted(c.args[4] for c in calls) == [2, 4]       # each segment's own N
    for c in calls:
        n = c.args[4]
        assert f'eligible_lines:={n}' in c.kwargs['base_filter']
        assert f'eligible_lines:={6 - n}' not in c.kwargs['base_filter']
        assert 'customer_group:=[`consumer`]' in c.kwargs['base_filter']


async def test_quantity_in_one_segment_does_not_restrict_the_other(monkeypatch):
    from unittest.mock import AsyncMock
    from isp.services.infra.chat import multi_intent
    monkeypatch.setattr(multi_intent, '_category_vectors_for_index', AsyncMock(return_value=(
        {'plans': [1.0, 0.0]}, {'plans': 'Plans'})))
    monkeypatch.setattr(multi_intent, 'resolve_concept_field', AsyncMock(return_value='price'))
    monkeypatch.setattr(multi_intent, 'text_constraint_filters', AsyncMock(return_value=([], [])))
    qty = AsyncMock(return_value=([{'id': 'a'}], 'ess'))
    monkeypatch.setattr(multi_intent.retrieval, 'search_context_at_quantity', qty)
    sorted_search = AsyncMock(return_value=([{'id': 'b'}], 'beyond'))
    monkeypatch.setattr(multi_intent.retrieval, 'search_context_sorted', sorted_search)
    result = await multi_intent.try_multi_intent_context(
        'cheapest essentials plan for 2 lines and cheapest beyond plan', 'index', 'title', None, None,
        'customer_group:=[`consumer`]', None, None,
        embed=AsyncMock(return_value=[1.0, 0.0]), confirm=None,
        generic_category_values=set(), multi_intent_split_enabled=True,
        unit_count_field='eligible_lines', unit_price_field='line_prices',
    )
    assert result is not None
    assert qty.call_count == 1 and qty.call_args.args[4] == 2
    assert 'eligible_lines:=2' in qty.call_args.kwargs['base_filter']
    beyond_filter = sorted_search.call_args.kwargs['price_filter'] or ''
    assert 'eligible_lines' not in beyond_filter                 # sibling's N not applied
    assert 'customer_group:=[`consumer`]' in beyond_filter        # truly shared filter still is


@pytest.mark.parametrize('text, expected', [
    ('without netflix or hulu', [('netflix', True), ('hulu', True)]),
    ('without hulu from verizon', [('hulu', True), ('verizon', False)]),
    ('no hulu but with netflix', [('hulu', True), ('netflix', False)]),
])
def test_negation_scope(text, expected):
    assert query_filters._constraint_words(text) == expected
