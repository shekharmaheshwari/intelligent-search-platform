# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Synonym rules on Typesense ≥ 30 (synonym sets).

Regression: ISP called the pre-30 per-collection endpoint
(/collections/{c}/synonyms), which Typesense 30 removed, so every list
returned [] and every save was a 500.

1. Unit tests against a fake client cover which set a rule is read from,
   written to and deleted from, and that linking keeps other sets.
2. A live test runs the real round trip, including that search applies the
   rule. It needs TYPESENSE_HOST (and TYPESENSE_API_KEY) and is skipped otherwise.
"""
from __future__ import annotations
import os
import uuid

import pytest
from typesense.exceptions import ObjectNotFound

from isp.services.infra import typesense_client as ts


# ── Fake client ──────────────────────────────────────────────────────────────

class _FakeSet:
    def __init__(self, store: dict, name: str):
        self._store, self._name = store, name

    def _items(self) -> list[dict]:
        if self._name not in self._store:
            raise ObjectNotFound(404, "Synonym index not found")
        return self._store[self._name]

    def retrieve(self):
        return {"name": self._name, "items": [dict(i) for i in self._items()]}

    def upsert(self, body):
        self._store[self._name] = [{"root": "", **i} for i in body["items"]]
        return {"name": self._name, **body}

    def upsert_item(self, item_id, item):
        items = self._items()
        saved = {"id": item_id, "root": "", **item}
        items[:] = [i for i in items if i["id"] != item_id] + [saved]
        return dict(saved)

    def delete_item(self, item_id):
        items = self._items()
        if not any(i["id"] == item_id for i in items):
            raise ObjectNotFound(404, "Not found")
        items[:] = [i for i in items if i["id"] != item_id]
        return {"id": item_id}


class _FakeCollection:
    def __init__(self, coll: dict):
        self._coll = coll

    def retrieve(self):
        return dict(self._coll)

    def update(self, body):
        self._coll.update(body)
        return body


class _FakeClient:
    def __init__(self, coll_name: str, linked: list[str] | None = None, sets: dict | None = None):
        self.sets: dict[str, list[dict]] = sets or {}
        self.coll = {"name": coll_name, "synonym_sets": list(linked or [])}
        self._coll_name = coll_name

    @property
    def synonym_sets(self):
        client = self

        class _Sets:
            def __getitem__(self, name):
                return _FakeSet(client.sets, name)
        return _Sets()

    @property
    def collections(self):
        client = self

        class _Colls:
            def __getitem__(self, name):
                assert name == client._coll_name
                return _FakeCollection(client.coll)
        return _Colls()


IDX = "abc123"
OWN = f"isp_{IDX}_synonyms"


@pytest.fixture
def fake(monkeypatch):
    def install(**kw) -> _FakeClient:
        client = _FakeClient(ts.collection_name(IDX), **kw)
        monkeypatch.setattr(ts, "get_client", lambda: client)
        return client
    return install


# ── Unit ─────────────────────────────────────────────────────────────────────

def test_set_name_is_per_index():
    assert ts.synonym_set_name(IDX) == OWN


def test_first_save_creates_and_links_the_index_set(fake):
    c = fake()
    rule = ts.upsert_synonym(IDX, "apple-iphone", ["apple", "iphone"])
    assert rule == {"id": "apple-iphone", "synonyms": ["apple", "iphone"]}   # no empty root
    assert c.coll["synonym_sets"] == [OWN]
    assert ts.list_synonyms(IDX) == [rule]


def test_one_way_rule_keeps_root(fake):
    fake()
    rule = ts.upsert_synonym(IDX, "tv", ["television", "telly"], root="tv")
    assert rule == {"id": "tv", "synonyms": ["television", "telly"], "root": "tv"}
    assert ts.list_synonyms(IDX) == [rule]


def test_update_replaces_the_rule(fake):
    fake()
    ts.upsert_synonym(IDX, "w", ["wire", "cable"])
    ts.upsert_synonym(IDX, "w", ["wire", "cable", "cord"])
    assert ts.list_synonyms(IDX) == [{"id": "w", "synonyms": ["wire", "cable", "cord"]}]


def test_linking_keeps_sets_already_on_the_collection(fake):
    c = fake(linked=["shared"], sets={"shared": []})
    ts.upsert_synonym(IDX, "w", ["wire", "cable"])
    assert c.coll["synonym_sets"] == ["shared", OWN]


def test_migrated_rule_is_listed_edited_in_place_and_deleted(fake):
    # e.g. a set Typesense created from pre-30 synonyms on upgrade
    c = fake(linked=["migrated"], sets={"migrated": [{"id": "old", "root": "", "synonyms": ["a", "b"]}]})
    assert ts.list_synonyms(IDX) == [{"id": "old", "synonyms": ["a", "b"]}]

    ts.upsert_synonym(IDX, "old", ["a", "b", "c"])
    assert c.sets["migrated"][0]["synonyms"] == ["a", "b", "c"]
    assert OWN not in c.sets                           # no duplicate in the index set
    assert c.coll["synonym_sets"] == ["migrated"]

    assert ts.delete_synonym(IDX, "old") is True
    assert ts.list_synonyms(IDX) == []


def test_delete_missing_rule_returns_false(fake):
    fake()
    assert ts.delete_synonym(IDX, "nope") is False


def test_list_with_no_linked_sets_is_empty(fake):
    fake()
    assert ts.list_synonyms(IDX) == []


def test_linked_set_that_was_deleted_is_skipped(fake):
    fake(linked=["gone", OWN], sets={OWN: [{"id": "w", "root": "", "synonyms": ["wire", "cable"]}]})
    assert ts.list_synonyms(IDX) == [{"id": "w", "synonyms": ["wire", "cable"]}]


# ── Live (real Typesense ≥ 30) ───────────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("TYPESENSE_HOST"), reason="needs TYPESENSE_HOST (Typesense ≥ 30)")
def test_live_round_trip_and_search_applies_the_rule(monkeypatch):
    monkeypatch.setattr(ts, "_client", None)   # build a client from the test env
    client = ts.get_client()
    idx = f"syntest{uuid.uuid4().hex[:8]}"
    coll = ts.collection_name(idx)
    client.collections.create({"name": coll, "fields": [{"name": "title", "type": "string"}]})
    try:
        client.collections[coll].documents.create({"id": "1", "title": "usb cable"})

        def found(q: str) -> int:
            return client.collections[coll].documents.search({"q": q, "query_by": "title"})["found"]

        assert found("wire") == 0
        assert ts.list_synonyms(idx) == []

        rule = ts.upsert_synonym(idx, "wire-cable", ["wire", "cable"])
        assert rule == {"id": "wire-cable", "synonyms": ["wire", "cable"]}
        assert ts.list_synonyms(idx) == [rule]
        assert found("wire") == 1                      # applied, no rebuild

        ts.upsert_synonym(idx, "tv", ["television"], root="tv")
        assert {r["id"] for r in ts.list_synonyms(idx)} == {"wire-cable", "tv"}

        assert ts.delete_synonym(idx, "wire-cable") is True
        assert ts.delete_synonym(idx, "wire-cable") is False
        assert found("wire") == 0
    finally:
        client.collections[coll].delete()
        try:
            client.synonym_sets[ts.synonym_set_name(idx)].delete()
        except ObjectNotFound:
            pass
        monkeypatch.setattr(ts, "_client", None)
