# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import AsyncMock, Mock
import pytest
from isp.services.domain.query_router import can_answer_locally
from isp.services.infra.chat import service


@pytest.mark.parametrize('query,expected', [
    ('iPhone 17 cases under $30', True),
    ('Show me wireless headphones', True),
    ('case and screen protector under $50 total', True),
    ('Why is this case better for hiking?', False),
    ('Compare headphones', False),
    ('Which case fits my needs?', False),
    ('Is this case waterproof', False),
    ('hello', False),
])
def test_conservative_routes(query, expected):
    assert can_answer_locally(query, [], ['Cases', 'Headphones', 'Screen Protectors'], bundles_enabled=True) is expected


def test_followups_and_disabled_bundles_use_existing_path():
    assert not can_answer_locally('case', [{'role': 'user', 'content': 'earlier'}], ['Cases'], bundles_enabled=True)
    assert not can_answer_locally('case and protector under $50 total', [], ['Cases'], bundles_enabled=False)


async def test_local_request_skips_rewrite_and_answer_without_provider(monkeypatch):
    monkeypatch.setattr(service, '_local_query_router_enabled', True)
    monkeypatch.setattr(service, '_llm_response_enabled', True)
    monkeypatch.setattr(service, '_smart_followup_enabled', True)
    monkeypatch.setattr(service, '_chat_provider', 'none')
    monkeypatch.setattr(service.ts, 'get_facets', lambda *args: {'category': [{'value': 'cases'}]})
    monkeypatch.setattr(service.concept_resolution, 'resolve_concept_field', AsyncMock(return_value=None))
    monkeypatch.setattr(service.multi_intent, 'try_multi_intent_context', AsyncMock(return_value=None))
    monkeypatch.setattr(service.retrieval, 'search_context', AsyncMock(return_value=([{'id': 'case'}], 'context')))
    rewrite = AsyncMock(side_effect=AssertionError('No LLM rewrite allowed'))
    answer = Mock(side_effect=AssertionError('No LLM answer allowed'))
    monkeypatch.setattr(service, '_llm_rewrite_query', rewrite)
    monkeypatch.setattr(service, '_route_llm_stream', answer)
    events = [e async for e in service.chat_stream('show me cases', [], 'index')]
    assert events == [{'type': 'products', 'products': [{'id': 'case'}]}]
    rewrite.assert_not_called()
    answer.assert_not_called()
    # Per-request routing must not change shared runtime configuration.
    assert service._llm_response_enabled is True
    events = [e async for e in service.chat_stream('why is this case better?', [], 'index')]
    assert events[0]['type'] == 'error'  # Needs configured provider; never silently routes locally.


async def test_setting_persists_and_restores(monkeypatch):
    from isp.services.domain import settings_service as settings
    stored = {}
    async def get(key):
        return stored.get(key)
    async def set_value(key, value):
        stored[key] = value
    monkeypatch.setattr(settings, 'get_setting', get)
    monkeypatch.setattr(settings, 'set_setting', set_value)
    monkeypatch.setattr(settings, '_local_query_router_enabled', False)
    apply = Mock()
    monkeypatch.setattr(service, 'set_chat_config', apply)
    assert (await settings.get_chat_config())['local_query_router'] is False
    await settings.set_chat_config(provider='none', model='', local_query_router=True)
    assert stored['chat_local_query_router'] == 'true'
    assert (await settings.get_chat_config())['local_query_router'] is True
    assert apply.call_args.kwargs['local_query_router'] is True
    monkeypatch.setattr(settings, '_local_query_router_enabled', False)
    await settings.load_chat_config()
    assert apply.call_args.kwargs['local_query_router'] is True
