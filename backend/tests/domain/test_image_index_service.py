# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for image search indexing: URL extraction, field detection, and the cache-aware run."""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from isp.models import IndexMeta
from isp.services.domain import image_index_service as svc
from isp.services.infra import image_embedding as img


# ── URL extraction ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ("https://cdn.example.com/a.jpg", "https://cdn.example.com/a.jpg"),
    ("  http://x.io/a.png ", "http://x.io/a.png"),
    (["", "ftp://no", "https://x.io/1.jpg", "https://x.io/2.jpg"], "https://x.io/1.jpg"),
    ([{"url": "https://x.io/obj.jpg"}], "https://x.io/obj.jpg"),
    ({"src": "https://x.io/src.webp"}, "https://x.io/src.webp"),
    ("not a url", None),
    (None, None),
    (42, None),
])
def test_extract_image_url_shapes(value, expected):
    assert svc.extract_image_url({"img": value}, "img") == expected


def test_extract_image_url_falls_back_to_metadata():
    doc = {"metadata": json.dumps({"images": ["https://x.io/m.jpg"]})}
    assert svc.extract_image_url(doc, "images") == "https://x.io/m.jpg"


def test_candidate_fields_ranks_by_coverage_and_ignores_page_url():
    docs = [
        {"id": "1", "url": "https://shop/p1", "image": "https://cdn/p1.jpg",
         "metadata": json.dumps({"images": ["https://cdn/a.png"]})},
        {"id": "2", "url": "https://shop/p2", "image": "https://cdn/p2.jpg"},
        {"id": "3", "url": "https://shop/p3", "link": "https://shop/p3"},
    ]
    out = svc.candidate_fields(docs)
    assert [c["field"] for c in out] == ["image", "images"]
    assert out[0]["coverage"] == pytest.approx(0.667, abs=1e-3)


# ── index_images with fakes ──────────────────────────────────────────────────

def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def run_env(monkeypatch):
    def setup(docs, cfg, cached=None, downloads=None):
        meta = IndexMeta(id="idx1", name="t", description="", created_at="x", image_search=cfg)
        state = {"updates": [], "embedded": [], "cache_put": {}, "merged": [], "field_dim": None}

        async def get_index(_):
            return meta

        async def merge(_, fields):
            state["merged"].append(fields)

        async def cache_get(model, urls):
            return {u: v for u, v in (cached or {}).items() if u in urls}

        async def cache_put(model, vectors):
            state["cache_put"].update(vectors)

        async def fetch(_client, url):
            return (downloads or {}).get(url)

        async def embed_documents(images, model):
            state["embedded"].append(len(images))
            return [[0.5] * 4 for _ in images]

        monkeypatch.setattr(svc.index_service, "get_index", get_index)
        monkeypatch.setattr(svc.index_service, "merge_image_search", merge)
        monkeypatch.setattr(img, "get_image_model", lambda: "voyage-multimodal-3")
        monkeypatch.setattr(img, "model_available", lambda m: True)
        monkeypatch.setattr(img, "cache_get", cache_get)
        monkeypatch.setattr(img, "cache_put", cache_put)
        monkeypatch.setattr(img, "fetch_image", fetch)
        monkeypatch.setattr(img, "embed_documents", embed_documents)
        monkeypatch.setattr(svc.ts, "ensure_img_vec_field",
                            lambda idx, dim: state.__setitem__("field_dim", dim))
        monkeypatch.setattr(svc.ts, "export_documents", lambda idx, fields, fb=None: docs)
        monkeypatch.setattr(svc.ts, "update_documents",
                            lambda idx, batch: (state["updates"].extend(batch), len(batch))[1])
        return state
    return setup


async def test_index_images_uses_cache_and_embeds_only_misses(run_env):
    docs = [
        {"id": "a", "image": "https://cdn/a.jpg"},
        {"id": "b", "image": "https://cdn/b.jpg"},
        {"id": "c", "image": "https://cdn/b.jpg"},     # same image as b: embedded once
        {"id": "d", "image": "https://cdn/broken.jpg"},
        {"id": "e"},                                   # no image
    ]
    state = run_env(
        docs, {"enabled": True, "field": "image"},
        cached={"https://cdn/a.jpg": [0.1] * 4},
        downloads={"https://cdn/b.jpg": _png(), "https://cdn/broken.jpg": b"not an image"},
    )
    result = await svc.index_images("idx1")

    assert result["state"] == "done"
    assert state["field_dim"] == 1024
    assert state["embedded"] == [1]                       # only b
    assert set(state["cache_put"]) == {"https://cdn/b.jpg"}
    assert {u["id"] for u in state["updates"]} == {"a", "b", "c"}
    assert result | {} == {**result, "docs": 5, "with_image": 4, "cached": 1,
                           "embedded": 1, "failed": 1, "indexed": 3}
    assert state["merged"][-1]["last_run"]["state"] == "done"


async def test_index_images_skips_when_disabled(run_env):
    state = run_env([{"id": "a", "image": "https://cdn/a.jpg"}], {"enabled": False, "field": "image"})
    assert (await svc.index_images("idx1"))["state"] == "skipped"
    assert state["updates"] == [] and state["merged"] == []


async def test_index_images_records_error_instead_of_raising(run_env, monkeypatch):
    state = run_env([{"id": "a", "image": "https://cdn/a.jpg"}], {"enabled": True, "field": "image"})
    monkeypatch.setattr(img, "model_available", lambda m: False)
    result = await svc.index_images("idx1")
    assert result["state"] == "error" and "not available" in result["error"]
    assert state["merged"][-1]["last_run"]["state"] == "error"


# ── background runner: busy lock ─────────────────────────────────────────────

def _meta_with_run(last_run):
    return IndexMeta(id="idx", name="n", description="", created_at="x",
                     image_search={"enabled": True, "field": "image", "last_run": last_run})


@pytest.mark.parametrize("last_run, expect_error", [
    ({"state": "running", "model": "voyage-multimodal-3"}, False),   # second click while indexing
    ({"state": "done", "model": "voyage-multimodal-3"}, True),       # rebuild/ingest holds the lock
    (None, True),
])
async def test_busy_lock_keeps_running_status(monkeypatch, last_run, expect_error):
    from isp.routers import indexes as router
    from isp.services.infra import job_lock

    writes = []

    async def acquire(idx_id):
        return None

    async def get_index(idx_id):
        return _meta_with_run(last_run)

    async def merge(idx_id, fields):
        writes.append(fields)

    monkeypatch.setattr(job_lock, "acquire", acquire)
    monkeypatch.setattr(router.index_service, "get_index", get_index)
    monkeypatch.setattr(router.index_service, "merge_image_search", merge)

    await router._run_image_indexing_locked("idx")
    if expect_error:
        assert writes[0]["last_run"]["state"] == "error"
    else:
        assert writes == [{"rerun": True}]      # running status kept; another pass queued


@pytest.mark.parametrize("last_run, stale", [
    ({"state": "done", "model": "old-model"}, True),
    ({"state": "done", "model": "voyage-multimodal-3"}, False),
    ({"state": "error", "error": "busy"}, False),                    # no model recorded
    ({"state": "running", "model": "old-model"}, False),
])
async def test_stale_only_for_finished_runs(monkeypatch, last_run, stale):
    from isp.routers import indexes as router

    async def no_docs(idx_id, limit=50):
        return []

    monkeypatch.setattr(router.image_embedding, "get_image_model", lambda: "voyage-multimodal-3")
    monkeypatch.setattr(router.image_embedding, "model_available", lambda m: True)
    monkeypatch.setattr(router.image_index_service, "sample_docs", no_docs)
    state = await router._image_search_state(_meta_with_run(last_run))
    assert state["stale"] is stale


# ── stale vectors: image field changed / image removed ───────────────────────

async def test_index_images_flags_new_vectors_and_clears_stale_ones(run_env):
    docs = [
        {"id": "a", "img_ok": True},                              # had a vector; new field missing
        {"id": "b", "photo": "https://cdn/b.jpg", "img_ok": True},  # still has an image
        {"id": "c", "photo": "https://cdn/c.jpg"},                 # first vector
        {"id": "d"},                                               # never had one: left alone
    ]
    state = run_env(docs, {"enabled": True, "field": "photo"},
                    cached={"https://cdn/b.jpg": [0.1] * 4, "https://cdn/c.jpg": [0.2] * 4})
    result = await svc.index_images("idx1")

    by_id = {u["id"]: u for u in state["updates"]}
    assert set(by_id) == {"a", "b", "c"}
    assert by_id["a"] == {"id": "a", "img_vec": None, "img_ok": False}
    assert by_id["b"]["img_ok"] is True and by_id["c"]["img_ok"] is True
    assert result["indexed"] == 2 and result["cleared"] == 1


async def test_index_images_clears_doc_whose_image_cannot_be_loaded(run_env):
    docs = [{"id": "a", "photo": "https://cdn/gone.jpg", "img_ok": True}]
    state = run_env(docs, {"enabled": True, "field": "photo"}, downloads={})
    result = await svc.index_images("idx1")
    assert state["updates"] == [{"id": "a", "img_vec": None, "img_ok": False}]
    assert result["failed"] == 1 and result["cleared"] == 1


def test_search_by_image_vec_filters_on_img_ok_with_legacy_fallback(monkeypatch):
    from isp.services.infra import typesense_client as ts
    sent = []

    class _MS:
        def perform(self, body, params):
            search = body["searches"][0]
            sent.append(search.get("filter_by"))
            if "img_ok" in (search.get("filter_by") or "") and legacy:
                return {"results": [{"error": "Could not find a filter field named `img_ok` in the schema."}]}
            return {"results": [{"found": 1, "hits": []}]}

    monkeypatch.setattr(ts, "get_client", lambda: type("C", (), {"multi_search": _MS()})())
    legacy = False
    ts.search_by_image_vec("idx", [0.1], 1, 10, filter_by="price:=[10]")
    assert sent == ["img_ok:true && (price:=[10])"]

    sent.clear()
    legacy = True
    assert ts.search_by_image_vec("idx", [0.1], 1, 10)["found"] == 1
    assert sent == ["img_ok:true", None]


# ── system-field protection ──────────────────────────────────────────────────

def test_image_fields_are_system_fields(monkeypatch):
    from isp.routers import indexes as router
    from isp.services.domain import manual_document_service
    from isp.services.infra import typesense_client as ts

    schema = {"fields": [
        {"name": "title", "type": "string"},
        {"name": "img_vec", "type": "float[]", "num_dim": 1024, "optional": True},
        {"name": "img_ok", "type": "bool", "optional": True},
    ]}
    col = type("Col", (), {"retrieve": lambda self: schema})()
    monkeypatch.setattr(ts, "get_client", lambda: type("C", (), {"collections": {"isp_idx": col}})())
    flags = {f["name"]: f["system"] for f in ts.get_all_fields("idx")}
    assert flags == {"title": False, "img_vec": True, "img_ok": True}

    assert {"img_vec", "img_ok"} <= router._FIELD_SYSTEM            # field editor refuses changes
    assert {"img_vec", "img_ok"} <= router._MANUAL_HIDDEN_FIELDS    # not in the manual-doc form
    assert {"img_vec", "img_ok"} <= manual_document_service._RESERVED


# ── sitemap ingest attaches image vectors ────────────────────────────────────

@pytest.mark.parametrize("ingest_state, expect_images", [("done", True), ("error", False)])
async def test_sitemap_ingest_runs_image_indexing(monkeypatch, ingest_state, expect_images):
    from isp.routers import indexes as router
    from isp.services.infra import job_lock

    calls = []

    async def acquire(idx_id):
        return "tok"

    async def release(idx_id, token):
        pass

    async def heartbeat(idx_id, token):
        pass

    async def noop(*a, **k):
        return []

    async def batched(idx_id, connector_cfg, cfg, ext_records, transform_exts, s):
        s.state = ingest_state

    async def index_images(idx_id, doc_ids=None):
        calls.append(idx_id)

    monkeypatch.setattr(job_lock, "acquire", acquire)
    monkeypatch.setattr(job_lock, "release", release)
    monkeypatch.setattr(job_lock, "heartbeat", heartbeat)
    monkeypatch.setattr(router.index_service, "set_status", noop)
    monkeypatch.setattr(router.extension_service, "list_index_extensions", noop)
    monkeypatch.setattr(router, "_ingest_sitemap_batched", batched)
    monkeypatch.setattr(router.image_index_service, "index_images", index_images)

    await router._run_ingest("idx", {"type": "sitemap", "url": "https://example.com/sitemap.xml"})
    assert calls == (["idx"] if expect_images else [])


# ── config changed while a run is in progress ────────────────────────────────

@pytest.fixture
def rerun_env(monkeypatch):
    """index_service fakes backed by one in-memory image_search dict."""
    from isp.routers import indexes as router
    from isp.services.infra import job_lock

    cfg = {"enabled": True, "field": "old", "last_run": {"state": "running"}}
    env = {"cfg": cfg, "fields_indexed": [], "lock_free": False, "during_run": None}

    async def get_index(idx_id):
        return IndexMeta(id="idx", name="n", description="", created_at="x", image_search=dict(cfg))

    async def merge(idx_id, fields):
        cfg.update(fields)

    async def index_images(idx_id, doc_ids=None):
        env["fields_indexed"].append(cfg["field"])
        if env["during_run"]:
            hook, env["during_run"] = env["during_run"], None
            await hook()
        cfg["last_run"] = {"state": "done"}
        return {"state": "done"}

    async def acquire(idx_id):
        return "tok" if env["lock_free"] else None

    async def release(idx_id, token):
        pass

    async def heartbeat(idx_id, token):
        pass

    for mod in (svc.index_service, router.index_service):
        monkeypatch.setattr(mod, "get_index", get_index)
        monkeypatch.setattr(mod, "merge_image_search", merge)
    monkeypatch.setattr(svc, "index_images", index_images)
    monkeypatch.setattr(job_lock, "acquire", acquire)
    monkeypatch.setattr(job_lock, "release", release)
    monkeypatch.setattr(job_lock, "heartbeat", heartbeat)
    return router, env


async def test_field_change_during_run_queues_another_pass(rerun_env):
    router, env = rerun_env
    cfg = env["cfg"]

    async def change_field_mid_run():
        # The user saves a new field while the first pass runs. Its background
        # request finds the lock held by the run in progress (lock_free=False).
        cfg["field"] = "new"
        await router._run_image_indexing_locked("idx")
        state = await router._image_search_state(await router.index_service.get_index("idx"))
        assert state["queued"] is True and state["last_run"]["state"] == "running"

    env["during_run"] = change_field_mid_run
    await svc.index_images_until_settled("idx")      # the run holding the lock

    assert env["fields_indexed"] == ["old", "new"]
    assert cfg[svc.RERUN_KEY] is False and cfg["last_run"]["state"] == "done"


async def test_rerun_runs_itself_if_the_old_run_already_released(rerun_env, monkeypatch):
    """The old run released the lock before seeing the flag: the request runs the pass itself."""
    from isp.services.infra import job_lock
    router, env = rerun_env
    answers = iter([None, "tok"])                     # busy, then free on the retry

    async def acquire(idx_id):
        return next(answers)

    monkeypatch.setattr(job_lock, "acquire", acquire)
    env["cfg"]["field"] = "new"
    await router._run_image_indexing_locked("idx")
    assert env["fields_indexed"] == ["new"]
    assert env["cfg"][svc.RERUN_KEY] is False


async def test_busy_because_of_rebuild_does_not_queue(rerun_env):
    router, env = rerun_env
    env["cfg"]["last_run"] = {"state": "done"}
    await router._run_image_indexing_locked("idx")
    assert env["fields_indexed"] == []
    assert env["cfg"]["last_run"]["state"] == "error"
    assert not env["cfg"].get(svc.RERUN_KEY)
