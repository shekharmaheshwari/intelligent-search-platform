from unittest.mock import AsyncMock, Mock
import pytest
from isp.services.infra.chat import comparison, service


@pytest.mark.parametrize('query', ['iphone 17 vs samsung s25', 'iphone 17 vs. samsung s25', 'compare iphone 17 and samsung s25', 'iphone 17 versus samsung s25'])
def test_parse(query):
    assert comparison.parse_comparison(query) == ('iphone 17', 'samsung s25')


def test_no_split_for_regular_or_ambiguous_queries():
    assert comparison.parse_comparison('case and headphones') is None
    assert comparison.parse_comparison('this vs that') is None
    assert comparison.parse_comparison('a vs b vs c') is None


def test_model_variants_are_not_substituted():
    cards = [{'title': 'Galaxy S25+', 'manufacturer': 'Samsung'},
             {'title': 'Galaxy S25 Ultra', 'manufacturer': 'Samsung'},
             {'title': 'Galaxy S25', 'manufacturer': 'Samsung'}]
    assert comparison.choose_product('samsung s25', cards) == cards[2]
    assert comparison.choose_product('samsung s25', cards[:2]) is None
    assert comparison.choose_product('samsung s25+', cards) == cards[0]


@pytest.mark.parametrize('use_llm', [False, True])
async def test_comparison_call_count_and_independent_retrieval(monkeypatch, use_llm):
    monkeypatch.setattr(service, '_llm_response_enabled', use_llm)
    monkeypatch.setattr(service, '_chat_provider', 'test' if use_llm else 'none')
    monkeypatch.setattr(service, '_smart_followup_enabled', True)
    monkeypatch.setattr(service, '_local_query_router_enabled', True)
    rewrite = AsyncMock(side_effect=AssertionError('No rewrite for named comparisons'))
    monkeypatch.setattr(service, '_llm_rewrite_query', rewrite)
    monkeypatch.setattr(comparison.concept_resolution, 'resolve_concept_field', AsyncMock(return_value=None))
    search = AsyncMock(side_effect=[
        ([{'id': 'a', 'title': 'iPhone 17', 'manufacturer': 'Apple', 'price': '799', 'reasoningData': {'facts': [{'key': 'operating_system', 'label': 'Operating system', 'value': 'iOS', 'source': 'os'}]}}], 'unused'),
        ([{'id': 'b', 'title': 'Galaxy S25', 'manufacturer': 'Samsung', 'price': '699', 'reasoningData': {'facts': [{'key': 'operating_system', 'label': 'Operating system', 'value': 'Android', 'source': 'os'}]}}], 'unused'),
    ])
    monkeypatch.setattr(comparison.retrieval, 'search_context', search)
    async def generate(*args):
        yield 'Grounded explanation'
    llm = Mock(side_effect=generate)
    monkeypatch.setattr(service, '_route_llm_stream', llm)
    events = [e async for e in service.chat_stream('iphone 17 vs samsung s25', [], 'phones')]
    assert [c['id'] for c in events[0]['products']] == ['a', 'b']
    assert [c.args[0] for c in search.call_args_list] == ['iphone 17', 'samsung s25']
    assert llm.call_count == int(use_llm)
    rewrite.assert_not_called()
    if use_llm:
        assert 'iOS' in llm.call_args.args[0] and 'Android' in llm.call_args.args[0]
    else:
        assert '| Feature |' in events[1]['content']
        assert 'iOS' in events[1]['content'] and 'Android' in events[1]['content']


async def test_missing_side_and_failed_search_are_explicit(monkeypatch):
    monkeypatch.setattr(comparison.concept_resolution, 'resolve_concept_field', AsyncMock(return_value=None))
    monkeypatch.setattr(comparison.retrieval, 'search_context', AsyncMock(side_effect=[([], ''), RuntimeError('down')]))
    cards, table = await comparison.retrieve_comparison(('iphone 17', 'samsung s25'), 'index', 'title', None, None)
    assert not cards
    assert 'No exact product match found' in table
    assert 'Search temporarily unavailable' in table


def test_accessories_do_not_stand_in_for_phone():
    assert comparison.choose_product('iphone 17', [{'title': 'Clear Case for iPhone 17', 'manufacturer': 'Apple'}]) is None
