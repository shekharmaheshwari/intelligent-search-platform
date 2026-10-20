from unittest.mock import AsyncMock
import pytest
from isp.services.domain.query_decomposition import decompose_query
from isp.services.domain.chat_intent_split import maybe_build_plans


@pytest.mark.parametrize('query, expected', [
    ('A waterproof case for my iPhone 17. Also need wireless headphones for running.',
     ['A waterproof case for my iPhone 17', 'wireless headphones for running']),
    ('iPhone 17 case under $25 and Galaxy S25 case under $40',
     ['iPhone 17 case under $25', 'Galaxy S25 case under $40']),
    ('case and screen protector, each under $30',
     ['case under $30', 'screen protector under $30']),
    ('case under $29.99. Also need Bluetooth 5.3 headphones.',
     ['case under $29.99', 'Bluetooth 5.3 headphones']),
])
def test_local_decomposition(query, expected):
    assert decompose_query(query, ['Cases And Covers', 'Screen Protectors', 'Headphones']) == ('split', expected)


@pytest.mark.parametrize('query', ['waterproof and durable case', 'case between $20 and $40', 'wireless headphones for running'])
def test_single_product_is_not_split(query):
    status, segments = decompose_query(query, ['Cases', 'Headphones'])
    assert status in ('single', 'fallback')
    assert segments == []


@pytest.mark.parametrize('query', ['case or headphones', 'case and headphones under $50 total', 'phone then a case that fits it'])
def test_unsupported_relationships(query):
    assert decompose_query(query, ['Cases', 'Headphones']) == ('unsupported', [])


def test_unknown_terms_use_legacy_classifier():
    assert decompose_query('airpods and case', ['Cases', 'Headphones']) == ('fallback', [])


async def test_same_category_preserves_both_devices_and_local_prices():
    segments = ['iPhone 17 case under $25', 'Galaxy S25 case under $40']
    plans = await maybe_build_plans(
        ' and '.join(segments), category_vectors={'cases': [1.0, 0.0]},
        label_by_category={'cases': 'Cases'}, embed=AsyncMock(return_value=[1.0, 0.0]),
        constraint_extractor=lambda q: 'price:<25' if '$25' in q else 'price:<40',
        decomposed_segments=segments,
    )
    assert plans is not None and len(plans) == 2
    assert plans[0].query == segments[0]
    assert plans[1].query == segments[1]
    assert 'price:<25' in plans[0].filter_by
    assert 'price:<40' in plans[1].filter_by


async def test_unclassified_intent_rejects_entire_structured_plan():
    plans = await maybe_build_plans(
        'case and headphones and unknown',
        category_vectors={'cases': [1.0, 0.0], 'headphones': [0.0, 1.0]},
        label_by_category={}, embed=AsyncMock(side_effect=[[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]]),
        constraint_extractor=lambda q: None,
        decomposed_segments=['case', 'headphones', 'unknown'],
    )
    assert plans is None


async def test_sentence_request_splits_without_llm():
    from isp.services.domain.chat_intent_split import split_segments

    query = 'A waterproof case for my iPhone 17. Also need wireless headphones for running.'
    assert split_segments(query) == [
        'A waterproof case for my iPhone 17', 'wireless headphones for running',
    ]
    embed = AsyncMock(side_effect=[[1.0, 0.0], [0.0, 1.0]])
    plans = await maybe_build_plans(
        query, category_vectors={'cases': [1.0, 0.0], 'headphones': [0.0, 1.0]},
        label_by_category={}, embed=embed, confirm=None,
        constraint_extractor=lambda q: None,
    )
    assert plans is not None
    assert [p.category for p in plans] == ['cases', 'headphones']
    assert plans[1].query == 'wireless headphones for running'
    assert embed.call_args_list[1].args == ('wireless headphones for running',)


def test_sentence_split_preserves_decimal_prices_and_device_versions():
    from isp.services.domain.chat_intent_split import split_segments

    assert split_segments('case under $29.99. Also need Bluetooth 5.3 headphones.') == [
        'case under $29.99', 'Bluetooth 5.3 headphones',
    ]
    assert split_segments('case under $29.99') == []


def test_missing_spacy_uses_legacy_fallback(monkeypatch):
    from isp.services.domain import query_decomposition

    def unavailable():
        raise ImportError('spaCy not installed')

    monkeypatch.setattr(query_decomposition, '_pipeline', unavailable)
    assert decompose_query('case and headphones', ['Cases', 'Headphones']) == ('fallback', [])


def test_catalog_vocabulary_is_not_phone_specific():
    assert decompose_query('oak table and red chairs', ['Tables', 'Chairs']) == (
        'split', ['oak table', 'red chairs'],
    )


async def test_exact_catalog_match_survives_low_embedding_similarity():
    from isp.services.domain.query_decomposition import match_categories

    query = 'A waterproof case for my iPhone 17. Also need wireless headphones for running.'
    labels = {'cases-&-covers': 'Cases And Covers', 'headphones': 'Headphones'}
    status, segments = decompose_query(query, list(labels.values()))
    assert status == 'split'
    matched = match_categories(segments, labels)
    assert matched == {0: 'cases-&-covers', 1: 'headphones'}
    embed = AsyncMock(side_effect=AssertionError('Exact matches must not require embeddings'))
    plans = await maybe_build_plans(
        query, category_vectors={key: [1.0, 0.0] for key in labels},
        label_by_category=labels, embed=embed, constraint_extractor=lambda q: None,
        decomposed_segments=segments, matched_categories=matched,
    )
    assert plans is not None and len(plans) == 2
    assert [p.category for p in plans] == list(labels)
    embed.assert_not_called()


def test_ambiguous_category_match_is_not_trusted():
    from isp.services.domain.query_decomposition import match_categories

    assert match_categories(['headphones with a case'], {'cases': 'Cases', 'headphones': 'Headphones'}) == {}
