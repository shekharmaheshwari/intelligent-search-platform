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
