# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the pure helpers in manual_document_service and its schema mapping."""
from __future__ import annotations

import pytest

from isp.services.domain import manual_document_service as m
from isp.routers.indexes import _ensure_isp_schema


def test_new_doc_id_is_manual_and_typesense_safe():
    doc_id = m.new_doc_id()
    assert m.is_manual_id(doc_id)
    assert all(c.isalnum() or c in "-_" for c in doc_id)


def test_connector_ids_are_not_manual():
    assert not m.is_manual_id("abc123")
    assert not m.is_manual_id("manualish")


def test_clean_payload_trims_and_drops_empty_and_reserved():
    out = m.clean_payload({
        "title": "  Spring sale  ", "content": "Body", "url": "  ",
        "tags": ["a", " ", "b "], "price": 0, "is_featured": False,
        "id": "evil", "vec": [1.0], "source_type": "rest", "_isp_type": "x",
        "empty_list": [], "none": None,
    })
    assert out == {
        "title": "Spring sale", "content": "Body",
        "tags": ["a", "b"], "price": 0, "is_featured": False,
    }


@pytest.mark.parametrize("missing", ["title", "content"])
def test_clean_payload_requires_title_and_content(missing):
    body = {"title": "t", "content": "c"}
    body[missing] = " "
    with pytest.raises(ValueError, match=missing):
        m.clean_payload(body)


def test_clean_payload_rejects_nested_objects():
    with pytest.raises(ValueError):
        m.clean_payload({"title": "t", "content": "c", "obj": {"a": 1}})


def test_schema_mapping_keeps_manual_marker_and_promotes_fields():
    payload = m.clean_payload({
        "title": "Campaign", "content": "Spring campaign page",
        "category": "marketing", "popularity": 5,
        "boost_queries_equal": ["spring sale"],
    })
    [doc] = _ensure_isp_schema([m.to_index_doc("manual-abc", payload)])
    assert doc["id"] == "manual-abc"
    assert doc["source_type"] == m.SOURCE_TYPE
    assert doc["category"] == "marketing"
    assert doc["popularity"] == 5
    assert doc["boost_queries_equal"] == ["spring sale"]
