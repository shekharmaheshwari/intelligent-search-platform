# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Indexing product images for image search.

Per index, ``image_search = {"enabled": bool, "field": str}`` names the doc
field that holds the product image URL (a string, a list of URLs, or objects
with a ``url``/``src`` key — the first one is used). ``index_images`` then:

1. makes sure the collection has an ``img_vec`` field sized for the image model;
2. exports each doc's image URL;
3. takes vectors from the Postgres cache, and downloads + embeds only the
   misses (in chunks, caching as it goes so an interrupted run keeps progress);
4. writes ``img_vec`` onto the docs with a partial update.

It runs after every rebuild and ingest (the rebuild drops the collection, and
the cache makes re-running cheap), after a manual doc is saved, and on demand.
Run stats are stored in ``image_search.last_run`` for the UI.
"""
from __future__ import annotations
import asyncio, json, logging, re, time

from isp.services.domain import chunking, index_service
from isp.services.infra import image_embedding as img
from isp.services.infra import typesense_client as ts

log = logging.getLogger(__name__)

_FETCH_CONCURRENCY = 8
_CHUNK = 64            # images downloaded + embedded per round
_UPDATE_BATCH = 200
_IMAGE_EXT = re.compile(r"\.(jpe?g|png|webp|gif|bmp|avif)(\?|#|$)", re.I)


def extract_image_url(doc: dict, field: str) -> str | None:
    """First usable http(s) image URL in ``doc[field]`` (falling back to the metadata blob)."""
    value = doc.get(field)
    if value is None and doc.get("metadata"):
        try:
            meta = json.loads(doc["metadata"]) if isinstance(doc["metadata"], str) else doc["metadata"]
            value = (meta or {}).get(field)
        except (ValueError, TypeError):
            value = None
    return _first_url(value)


def _first_url(value) -> str | None:
    if isinstance(value, str):
        v = value.strip()
        return v if v.startswith(("http://", "https://")) else None
    if isinstance(value, dict):
        return _first_url(value.get("url") or value.get("src") or value.get("href"))
    if isinstance(value, list):
        for item in value:
            url = _first_url(item)
            if url:
                return url
    return None


def candidate_fields(docs: list[dict]) -> list[dict]:
    """
    Fields (top-level or inside ``metadata``) that hold image URLs in a sample of
    docs, best first: ``[{"field": str, "coverage": float}]``. A URL counts when
    it has an image extension or the field name mentions image/img/photo/thumb.
    """
    counts: dict[str, int] = {}
    for doc in docs:
        flat = dict(doc)
        if isinstance(doc.get("metadata"), str):
            try:
                for k, v in (json.loads(doc["metadata"]) or {}).items():
                    flat.setdefault(k, v)
            except (ValueError, TypeError):
                pass
        for key, value in flat.items():
            if key in ("id", "vec", "metadata", "url") or key in ts.IMAGE_SYSTEM_FIELDS:
                continue
            url = _first_url(value)
            if url and (_IMAGE_EXT.search(url) or re.search(r"image|img|photo|thumb|picture", key, re.I)):
                counts[key] = counts.get(key, 0) + 1
    total = max(len(docs), 1)
    return [{"field": k, "coverage": round(n / total, 3)}
            for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


async def _embed_missing(urls: list[str], model: str, stats: dict) -> dict[str, list[float]]:
    """Download, prepare and embed ``urls`` in chunks; caches each chunk as it completes."""
    vectors: dict[str, list[float]] = {}
    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)
    async with img.http_client() as client:
        async def load(url: str):
            async with sem:
                data = await img.fetch_image(client, url)
            if data is None:
                return url, None
            try:
                return url, await asyncio.to_thread(img.prepare_image, data)
            except img.ImageError:
                return url, None

        for i in range(0, len(urls), _CHUNK):
            loaded = await asyncio.gather(*(load(u) for u in urls[i:i + _CHUNK]))
            ok = [(u, im) for u, im in loaded if im is not None]
            stats["failed"] += len(loaded) - len(ok)
            if not ok:
                continue
            vecs = await img.embed_documents([im for _, im in ok], model)
            chunk = {u: v for (u, _), v in zip(ok, vecs) if v}
            await img.cache_put(model, chunk)
            vectors.update(chunk)
            stats["embedded"] += len(chunk)
    return vectors


async def index_images(idx_id: str, doc_ids: list[str] | None = None) -> dict:
    """
    Embed product images for an index (or just ``doc_ids``) into ``img_vec``.
    Never raises: failures are logged and recorded in ``image_search.last_run``.
    The caller must hold the index job lock when running alongside rebuilds.
    """
    meta = await index_service.get_index(idx_id)
    if not meta or meta.is_external or meta.external_collection:
        return {"state": "skipped", "reason": "not an ISP-managed index"}
    cfg = meta.image_search or {}
    field = (cfg.get("field") or "").strip()
    if not cfg.get("enabled") or not field:
        return {"state": "skipped", "reason": "image search is off for this index"}
    model = img.get_image_model()

    stats = {"docs": 0, "with_image": 0, "cached": 0, "embedded": 0, "failed": 0}
    started = int(time.time())
    partial = doc_ids is not None
    if not partial:
        await index_service.merge_image_search(idx_id, {"last_run": {
            "state": "running", "model": model, "started_at": started, **stats,
        }})
    try:
        if not model:
            raise RuntimeError("No image embedding model is selected in Settings")
        if not img.model_available(model):
            raise RuntimeError(f"Image model {model} is not available — check its API key")

        await asyncio.to_thread(ts.ensure_img_vec_field, idx_id, img.DIM_BY_IMAGE_MODEL[model])
        filter_by = None
        if partial:
            if not doc_ids:
                return {"state": "skipped", "reason": "no documents"}
            filter_by = "id:[" + ",".join(f"`{d.replace('`', '')}`" for d in doc_ids) + "]"
        # One image per source document: chunks after the first are skipped.
        docs = await asyncio.to_thread(
            ts.export_documents, idx_id, ["id", field, "metadata", ts.IMG_OK_FIELD],
            chunking.heads_only(filter_by),
        )
        stats["docs"] = len(docs)

        url_by_doc = {d["id"]: u for d in docs if (u := extract_image_url(d, field))}
        stats["with_image"] = len(url_by_doc)
        urls = list(dict.fromkeys(url_by_doc.values()))

        vectors = await img.cache_get(model, urls)
        stats["cached"] = len(vectors)
        missing = [u for u in urls if u not in vectors]
        if missing:
            vectors.update(await _embed_missing(missing, model, stats))

        updates = [{"id": d, ts.IMG_VEC_FIELD: vectors[u], ts.IMG_OK_FIELD: True}
                   for d, u in url_by_doc.items() if u in vectors]
        # Docs that had a vector but now have none (image field changed, image
        # removed, or it can no longer be loaded) must stop matching image
        # searches. Nulling img_vec alone does not remove it from Typesense's
        # vector index, so img_ok is turned off as well (search filters on it).
        with_vec = {u["id"] for u in updates}
        clears = [{"id": d["id"], ts.IMG_VEC_FIELD: None, ts.IMG_OK_FIELD: False}
                  for d in docs if d.get(ts.IMG_OK_FIELD) is True and d["id"] not in with_vec]
        written = cleared = 0
        for i in range(0, len(updates), _UPDATE_BATCH):
            written += await asyncio.to_thread(ts.update_documents, idx_id, updates[i:i + _UPDATE_BATCH])
        for i in range(0, len(clears), _UPDATE_BATCH):
            cleared += await asyncio.to_thread(ts.update_documents, idx_id, clears[i:i + _UPDATE_BATCH])
        stats["indexed"] = written
        stats["cleared"] = cleared
        result = {"state": "done", "model": model, "started_at": started,
                  "finished_at": int(time.time()), **stats}
    except Exception as exc:
        log.error("Image indexing failed: index_id=%s", idx_id, exc_info=True)
        result = {"state": "error", "model": model, "started_at": started,
                  "finished_at": int(time.time()), "error": str(exc), **stats}

    if not partial or result["state"] == "error":
        await index_service.merge_image_search(idx_id, {"last_run": result})
    return result


# ``image_search.rerun``: set when the config changes (or a re-index is asked
# for) while a run is in progress. The run holding the job lock repeats until
# it is clear, so the latest field is always the one indexed last.
RERUN_KEY = "rerun"
_MAX_RERUNS = 3


async def request_rerun(idx_id: str) -> None:
    """Ask the run in progress to index again once it finishes."""
    await index_service.merge_image_search(idx_id, {RERUN_KEY: True})


async def rerun_requested(idx_id: str) -> bool:
    meta = await index_service.get_index(idx_id)
    return bool(((meta.image_search if meta else None) or {}).get(RERUN_KEY))


async def index_images_until_settled(idx_id: str) -> dict:
    """
    Full ``index_images`` run, repeated while a rerun was requested during it
    (e.g. the image field changed mid-run). The caller holds the job lock.
    Never raises.
    """
    result: dict = {}
    for _ in range(1 + _MAX_RERUNS):
        try:
            if await rerun_requested(idx_id):
                # Clear the flag in the same write that marks this pass as running,
                # so a poller never sees "done" with nothing queued in between.
                await index_service.merge_image_search(idx_id, {
                    RERUN_KEY: False,
                    "last_run": {"state": "running", "model": img.get_image_model(),
                                 "started_at": int(time.time())},
                })
        except Exception:
            log.warning("could not clear image rerun flag: index_id=%s", idx_id, exc_info=True)
        result = await index_images(idx_id)
        try:
            if not await rerun_requested(idx_id):
                break
        except Exception:
            break
        log.info("image indexing: config changed during the run, indexing again: index_id=%s", idx_id)
    return result


async def sample_docs(idx_id: str, limit: int = 50) -> list[dict]:
    """A sample of docs (no vectors) for detecting which field holds image URLs."""
    raw = await asyncio.to_thread(
        ts.list_documents, idx_id, 1, limit, True,
    )
    return [h.get("document", {}) for h in raw.get("hits", [])]
