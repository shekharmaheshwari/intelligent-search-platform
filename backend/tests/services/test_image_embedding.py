# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for image loading guards and query embedding in image_embedding, and request helpers in the search router."""
from __future__ import annotations

import base64
import io

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

from isp.routers import search as search_router
from isp.services.infra import image_embedding as img


def _img_bytes(fmt="PNG", size=(1200, 800)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buf, fmt)
    return buf.getvalue()


# ── prepare_image ────────────────────────────────────────────────────────────

def test_prepare_image_downsizes_and_converts_to_rgb():
    out = img.prepare_image(_img_bytes())
    assert out.mode == "RGB"
    assert max(out.size) == img.MAX_SIDE
    assert out.size == (512, 341)


@pytest.mark.parametrize("data", [b"", b"plain text", b"\x89PNG\r\n\x1a\nbroken"])
def test_prepare_image_rejects_non_images(data):
    with pytest.raises(img.ImageError):
        img.prepare_image(data)


def test_prepare_image_rejects_unsupported_format():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "TIFF")
    with pytest.raises(img.ImageError, match="unsupported"):
        img.prepare_image(buf.getvalue())


# ── fetch_image guards ───────────────────────────────────────────────────────

@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "localhost"])
def test_private_hosts_are_not_public(host):
    assert img._host_is_public(host) is False


@pytest.mark.parametrize("url", ["ftp://x.io/a.jpg", "file:///etc/passwd", "http://127.0.0.1/a.jpg",
                                 "http://169.254.169.254/latest/meta-data"])
async def test_fetch_image_refuses_bad_schemes_and_internal_hosts(url):
    async with httpx.AsyncClient() as client:
        assert await img.fetch_image(client, url) is None


async def test_fetch_image_checks_redirect_targets(monkeypatch):
    """A public URL redirecting to an internal address must not be followed."""
    monkeypatch.setattr(img, "_host_is_public", lambda host: host == "cdn.example.com")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.example.com":
            return httpx.Response(302, headers={"location": "http://10.0.0.1/secret.png"})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_img_bytes())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await img.fetch_image(client, "https://cdn.example.com/a.png") is None


async def test_fetch_image_follows_public_redirect_and_rejects_non_images(monkeypatch):
    monkeypatch.setattr(img, "_host_is_public", lambda host: True)
    png = _img_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old.png":
            return httpx.Response(301, headers={"location": "/new.png"})
        if request.url.path == "/new.png":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=png)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await img.fetch_image(client, "https://cdn.example.com/old.png") == png
        assert await img.fetch_image(client, "https://cdn.example.com/page") is None


# ── embed_query ──────────────────────────────────────────────────────────────

class _FakeVoyage:
    def __init__(self):
        self.calls = []

    async def multimodal_embed(self, inputs, model, input_type):
        self.calls.append((inputs, model, input_type))
        return type("R", (), {"embeddings": [[0.1, 0.2]]})()


@pytest.fixture
def voyage(monkeypatch):
    fake = _FakeVoyage()
    monkeypatch.setattr(img, "_voyage", lambda: fake)
    monkeypatch.setattr(img, "model_available", lambda m: True)
    img._query_lru.clear()
    return fake


async def test_embed_query_combines_image_and_text_and_caches(voyage):
    data = _img_bytes()
    assert await img.embed_query(data, " in black ", "voyage-multimodal-3") == [0.1, 0.2]
    await img.embed_query(data, "in black", "voyage-multimodal-3")      # cache hit
    assert len(voyage.calls) == 1
    inputs, model, input_type = voyage.calls[0]
    assert input_type == "query" and model == "voyage-multimodal-3"
    assert isinstance(inputs[0][0], Image.Image) and inputs[0][1] == "in black"


async def test_embed_query_requires_something_and_limits_size(voyage):
    with pytest.raises(img.ImageError):
        await img.embed_query(None, "  ", "voyage-multimodal-3")
    with pytest.raises(img.ImageError, match="5 MB"):
        await img.embed_query(b"x" * (img.MAX_QUERY_BYTES + 1), "", "voyage-multimodal-3")


async def test_embed_query_requires_configured_model(monkeypatch):
    with pytest.raises(RuntimeError):
        await img.embed_query(b"x", "", "")


def test_set_image_model_validates():
    with pytest.raises(ValueError):
        img.set_image_model("not-a-model")


# ── search router helpers ────────────────────────────────────────────────────

def test_decode_image_accepts_data_url_and_plain_base64():
    raw = b"\x89PNGdata"
    b64 = base64.b64encode(raw).decode()
    assert search_router._decode_image(f"data:image/png;base64,{b64}") == raw
    assert search_router._decode_image(b64) == raw
    assert search_router._decode_image("") is None


@pytest.mark.parametrize("bad", ["data:image/png,notbase64", "%%%not-base64%%%"])
def test_decode_image_rejects_bad_input(bad):
    with pytest.raises(HTTPException) as exc:
        search_router._decode_image(bad)
    assert exc.value.status_code == 400


def test_image_filter_sanitises_fields_and_values(monkeypatch):
    monkeypatch.setattr(search_router.ts, "get_all_fields", lambda idx: [
        {"name": "category", "type": "string"}, {"name": "price", "type": "float"},
    ])
    out = search_router._image_filter("idx", {
        "category": ["shoes", "ba`d"],
        "price": ["10", "1) || true"],
        "unknown": ["x"],
        "bad field": ["x"],
    })
    assert out == "category:=[`shoes`,`bad`] && price:=[10]"
    assert search_router._image_filter("idx", {}) is None


@pytest.mark.parametrize("cfg, model, expected", [
    ({"enabled": True, "last_run": {"state": "done", "model": "m"}}, "m", True),
    ({"enabled": True, "last_run": {"state": "running", "model": "m"}}, "m", False),   # not indexed yet
    ({"enabled": True, "last_run": {"state": "done", "model": "old"}}, "m", False),    # stale vectors
    ({"enabled": False, "last_run": {"state": "done", "model": "m"}}, "m", False),
    ({"enabled": True, "last_run": {"state": "done", "model": "m"}}, "", False),       # model off
])
async def test_image_search_available(monkeypatch, cfg, model, expected):
    from isp.models import IndexMeta
    meta = IndexMeta(id="idx", name="n", description="", created_at="x", image_search=cfg)

    async def resolve(idx_id):
        return meta

    monkeypatch.setattr(search_router, "_resolve_index", resolve)
    monkeypatch.setattr(search_router.image_embedding, "get_image_model", lambda: model)
    monkeypatch.setattr(search_router.image_embedding, "model_available", lambda m: True)
    assert await search_router.image_search_available("idx") == {"enabled": expected}
