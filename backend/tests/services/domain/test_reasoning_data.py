# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from isp.services.domain.reasoning_data import validate_reasoning_data, read_reasoning_data, display_fact
from isp.services.infra.chat.retrieval import _hit_to_card_and_block


def fact(key='custom_attribute', value=False, unit=''):
    return dict(key=key, label='Custom attribute', value=value, unit=unit, source='supplier.custom')


@pytest.mark.parametrize('value', [False, 0, 'Low indirect light', [1, 2, 3]])
def test_generic_values_are_preserved(value):
    raw = {'facts': [fact(value=value)]}
    assert validate_reasoning_data(raw) == raw


@pytest.mark.parametrize('raw', [None, {'facts': [fact(), fact()]}, {'facts': [fact(value=float('nan'))]},
    {'facts': [fact(value={'bad': 1})]}, {'facts': [fact(value='x'*2001)]},
    {'facts': [dict(key='x', label='X', value='y')]}, {'facts': [fact(key='Bad Key')]}])
def test_invalid_payload_is_rejected(raw):
    with pytest.raises((ValueError, TypeError)):
        validate_reasoning_data(raw)
    assert read_reasoning_data(raw) == {'facts': []}


def test_retrieval_includes_extension_facts_in_card_and_general_llm_context():
    data = {'facts': [fact(key='care_light', value='Indirect light')]}
    card, context = _hit_to_card_and_block({'document': {
        'id': 'plant', 'title': 'Fern', 'content': 'A fern',
        'metadata': json.dumps({'reasoningData': data}),
    }}, 1)
    assert card['reasoningData'] == data
    assert 'Indirect light' in context and 'supplier.custom' in context
    assert 'comparison_specs' not in card


def test_units_are_preserved_not_converted():
    assert display_fact(fact(value=24, unit='hours')) == '24 hours'
    assert display_fact(fact(value=1440, unit='minutes')) == '1440 minutes'


def test_ingestion_keeps_valid_facts_in_metadata_only():
    from isp.routers.indexes import _ensure_isp_schema
    data = {'facts': [fact(value=0)]}
    doc = _ensure_isp_schema([{'id': 'test', 'title': 'Product', 'reasoningData': data}])[0]
    assert json.loads(doc['metadata'])['reasoningData'] == data
    assert 'reasoningData' not in doc
    invalid = _ensure_isp_schema([{'title': 'Product', 'reasoningData': 'bad'}])[0]
    assert 'reasoningData' not in json.loads(invalid.get('metadata', '{}'))
    assert 'reasoningData' not in invalid


def test_catalog_path_preserves_reasoning_in_metadata():
    from isp.routers.indexes import _generic_catalog_mapper
    data = {'facts': [fact()]}
    doc = _generic_catalog_mapper({'title': 'Product', 'reasoningData': data}, 1)
    assert 'reasoningData' not in doc
    assert json.loads(doc['metadata'])['reasoningData'] == data
