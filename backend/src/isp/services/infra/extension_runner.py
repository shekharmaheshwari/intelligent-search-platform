"""
JavaScript extension execution engine using PyMiniRacer (V8).

Each enabled extension is compiled into its own V8 isolate once per ingest
batch, then called for every document in that batch.  This is the same
sandboxing model used by Coveo IPE: no filesystem, no network, no Node.js
APIs — only ECMAScript built-ins.

Thread model: PyMiniRacer's ``MiniRacer.call`` is synchronous.  ``apply_extensions``
wraps the synchronous loop in ``loop.run_in_executor(None, ...)`` so it runs in
a thread pool without blocking the asyncio event loop.

Error handling:
- If an extension fails to *compile*, it is skipped for the entire batch (logged
  as ERROR).
- If an extension raises a *runtime* error for a specific document, that document
  is kept in its current state and execution continues (logged as ERROR).  This
  ensures a single bad document cannot abort an entire ingest.
"""
from __future__ import annotations
import asyncio, logging

log = logging.getLogger(__name__)

try:
    from py_mini_racer import MiniRacer
    _MR_AVAILABLE = True
except ImportError:
    _MR_AVAILABLE = False
    log.warning("py-mini-racer not installed — JS extensions will be skipped")


def _run_sync(docs: list[dict], extension_records: list[dict]) -> list[dict]:
    """Apply enabled extensions in run_order to every document. Runs synchronously in a thread."""
    enabled = [e for e in extension_records if e.get("enabled", True)]
    if not enabled or not docs:
        return docs

    if not _MR_AVAILABLE:
        log.warning("py-mini-racer unavailable — skipping %d extension(s)", len(enabled))
        return docs

    # Compile each extension into its own V8 isolate once, reuse across all docs
    contexts: list[tuple[str, object]] = []
    for ext in enabled:
        name = ext.get("name", ext.get("id", "?"))
        code = ext.get("code", "")
        try:
            ctx = MiniRacer()
            ctx.eval(code)
            contexts.append((name, ctx))
            log.debug("Extension '%s' compiled OK", name)
        except Exception as exc:
            log.error("Extension '%s' failed to compile: %s", name, exc)
            contexts.append((name, None))

    results: list[dict] = []
    for doc in docs:
        current: dict = dict(doc)
        for name, ctx in contexts:
            if ctx is None:
                continue
            try:
                result = ctx.call("transform", current)  # type: ignore[union-attr]
                if isinstance(result, dict):
                    current = result
                else:
                    log.warning("Extension '%s' returned non-dict for doc '%s' — keeping original", name, doc.get("id", "?"))
            except Exception as exc:
                log.error("Extension '%s' runtime error on doc '%s': %s", name, doc.get("id", "?"), exc)
        results.append(current)
    return results


async def apply_extensions(docs: list[dict], extension_records: list[dict]) -> list[dict]:
    """
    Async entry point — runs V8 transforms in a thread executor so the event loop
    is not blocked during JS execution.
    """
    if not docs or not extension_records:
        return docs
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_sync, docs, extension_records)


def _filter_urls_sync(urls: list[str], extension_records: list[dict]) -> list[str]:
    """
    Apply ``filterUrl(url)`` from each extension that defines it, in run_order.

    Extensions that do not define ``filterUrl`` are silently skipped — only
    ``transform`` is mandatory.  A falsy return value drops the URL; errors
    on individual URLs keep the URL (same tolerant contract as ``_run_sync``).
    """
    enabled = [e for e in extension_records if e.get("enabled", True)]
    if not enabled or not urls:
        return urls

    if not _MR_AVAILABLE:
        log.warning("py-mini-racer unavailable — skipping filterUrl for %d extension(s)", len(enabled))
        return urls

    # Compile each extension once; record whether it exports filterUrl.
    contexts: list[tuple[str, object, bool]] = []
    for ext in enabled:
        name = ext.get("name", ext.get("id", "?"))
        code = ext.get("code", "")
        try:
            ctx = MiniRacer()
            ctx.eval(code)
            has_filter = ctx.eval("typeof filterUrl") == "function"
            contexts.append((name, ctx, has_filter))
        except Exception as exc:
            log.error("Extension '%s' failed to compile (filterUrl pass): %s", name, exc)
            contexts.append((name, None, False))

    result: list[str] = []
    for url in urls:
        keep = True
        for name, ctx, has_filter in contexts:
            if ctx is None or not has_filter:
                continue
            try:
                if not ctx.call("filterUrl", url):  # type: ignore[union-attr]
                    keep = False
                    break
            except Exception as exc:
                # A runtime error on one URL must not drop it — prefer false-positive crawl.
                log.error("Extension '%s' filterUrl error on '%s': %s", name, url, exc)
        if keep:
            result.append(url)
    return result


async def filter_urls(urls: list[str], extension_records: list[dict]) -> list[str]:
    """
    Async entry point — runs ``filterUrl`` hooks in a thread executor.

    Called before the crawl cap so cheap JS filtering reduces the pages
    actually fetched, not just the pages indexed.
    """
    if not urls or not extension_records:
        return urls
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _filter_urls_sync, urls, extension_records)
