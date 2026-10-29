# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Sitemap crawler: fetch a sitemap XML, extract page URLs, and scrape each page.

Crawl strategy:
1. Fetch the sitemap XML from ``cfg.url`` (gzipped ``.xml.gz`` is fine).  A
   ``<sitemapindex>`` is followed recursively (up to ``_SITEMAP_MAX_DEPTH`` levels)
   and every child sitemap's page URLs are merged, de-duplicated, in order.
2. Apply include/exclude substring list filters to the URL list.
3. Run the ``filterUrl()`` JS extension hook to drop unwanted URLs before fetching.
4. Truncate to ``cfg.max_pages`` (max 200 000, default 50).
5. Fetch pages with ``min(cfg.max_workers, ceil(pages / cfg.pages_per_worker))``
   workers pulling from one shared queue, each pausing 0.1 s between requests.
   A ``Crawl-delay`` in the site's robots.txt drops this to one worker at that
   delay.  A 429/503 pauses *every* worker (Retry-After, else exponential backoff)
   and the page is retried up to ``_MAX_RETRIES`` times.
6. Strip navigation/chrome elements (script, style, nav, footer, header, aside)
   then extract content from the configured selector or a fallback cascade.
   Parsing runs in a pool of worker *processes* (one per CPU, at most one per
   crawl worker): BeautifulSoup is CPU-bound, so threads would serialise on the
   GIL and cap the whole crawl at one parse at a time however many workers fetch.

``crawl_batches`` hands pages to the caller in batches while the crawl is still
running, so large sitemaps are embedded and indexed incrementally instead of
being held in memory; ``crawl`` collects everything for small callers.

OG tags (og:title, og:description) are preferred over plain <title> / meta name tags
so publisher-curated summaries lead the embedded text.  All meta tags are captured into
a ``metadata`` JSON field; ``cfg.meta_fields`` promotes selected ones to top-level fields.

Content is capped at 4 000 characters to keep Typesense document sizes reasonable
and avoid hitting embedding token limits.

Page IDs are MD5 hashes of the URL, truncated to 16 hex chars.  This means
re-crawling the same URL overwrites the existing document (upsert behaviour).
"""
from __future__ import annotations
import asyncio, gzip, hashlib, json, logging, math, multiprocessing, os, time
from concurrent.futures import ProcessPoolExecutor
from typing import Awaitable, Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup
from isp.models import SitemapConnectorConfig

log = logging.getLogger(__name__)

# Ordered fallback list of CSS selectors used when the user has not configured
# a custom content_selector.  The first selector that matches a non-empty element wins.
_CONTENT_SELECTORS = ["article", "main", "[role='main']", ".content", "#content", ".post-content", "body"]

_POLITE_DELAY = 0.1          # seconds each worker waits between its own requests
_SITEMAP_MAX_DEPTH = 3       # sitemap-index nesting levels followed
_SITEMAP_FETCH_PARALLEL = 4  # child sitemaps fetched at once
_MAX_RETRIES = 3             # extra attempts for a page answered with 429/503
_MAX_BACKOFF = 60.0          # cap on any single pause, including Retry-After


def parse_process_count(n_workers: int) -> int:
    """Parser processes: one per CPU, never more than there are crawl workers."""
    return max(1, min(n_workers, os.cpu_count() or 1))


def _parse_sitemap_urls(xml: str) -> list[str]:
    """Extract all ``<loc>`` URLs from a sitemap XML string."""
    soup = BeautifulSoup(xml, "lxml-xml")
    return [loc.get_text(strip=True) for loc in soup.find_all("loc")]


def _parse_sitemap_index(xml: str) -> list[str] | None:
    """Child sitemap URLs when ``xml`` is a ``<sitemapindex>``, else None."""
    soup = BeautifulSoup(xml, "lxml-xml")
    if not soup.find("sitemapindex"):
        return None
    return [loc.get_text(strip=True) for sm in soup.find_all("sitemap")
            if (loc := sm.find("loc"))]


async def _fetch_sitemap(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch a sitemap, transparently un-gzipping ``.xml.gz`` files."""
    try:
        r = await client.get(url, timeout=30, follow_redirects=True)
        r.raise_for_status()
    except Exception:
        log.debug("sitemap fetch failed for url=%s", url, exc_info=True)
        return None
    body = r.content
    # httpx already undoes Content-Encoding: gzip; this handles gzip *files*.
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError:
            log.warning("sitemap %s looks gzipped but could not be decompressed", url)
            return None
    return body.decode("utf-8", errors="replace")


async def _collect_page_urls(client: httpx.AsyncClient, url: str, depth: int = 0) -> list[str]:
    """
    Page URLs listed by ``url``, following sitemap indexes recursively.

    Raises ValueError only when the root sitemap is unreachable; an unreachable
    child sitemap is logged and skipped so one broken file doesn't sink the crawl.
    """
    xml = await _fetch_sitemap(client, url)
    if not xml:
        if depth == 0:
            raise ValueError(f"Could not fetch sitemap: {url}")
        log.warning("Skipping unreachable child sitemap %s", url)
        return []

    children = _parse_sitemap_index(xml)
    if children is None:
        return _parse_sitemap_urls(xml)
    if depth >= _SITEMAP_MAX_DEPTH:
        log.warning("Sitemap index nesting deeper than %d at %s — not followed", _SITEMAP_MAX_DEPTH, url)
        return []

    sem = asyncio.Semaphore(_SITEMAP_FETCH_PARALLEL)

    async def one(child: str) -> list[str]:
        async with sem:
            return await _collect_page_urls(client, child, depth + 1)

    nested = await asyncio.gather(*(one(c) for c in children))
    return [u for urls in nested for u in urls]


def _round_up_crawl_delays(lines: list[str]) -> list[str]:
    """RobotFileParser drops non-integer Crawl-delay values (e.g. ``0.5``); round them
    up to whole seconds instead, erring on the polite side."""
    out = []
    for line in lines:
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "crawl-delay":
            try:
                line = f"{key}: {math.ceil(float(value.split('#')[0]))}"
            except ValueError:
                pass
        out.append(line)
    return out


async def _robots_crawl_delay(client: httpx.AsyncClient, sitemap_url: str, ua: str) -> float | None:
    """``Crawl-delay`` robots.txt asks of ``ua`` on the sitemap's host, if any."""
    parts = urlsplit(sitemap_url)
    try:
        r = await client.get(f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=10)
        if r.status_code != 200:
            return None
        rp = RobotFileParser()
        rp.parse(_round_up_crawl_delays(r.text.splitlines()))
        delay = rp.crawl_delay(ua)
        return float(delay) if delay else None
    except Exception:
        log.debug("robots.txt lookup failed for %s", sitemap_url, exc_info=True)
        return None


def worker_count(n_urls: int, pages_per_worker: int, max_workers: int) -> int:
    """One worker per ``pages_per_worker`` pages, at least 1, never above ``max_workers``."""
    return max(1, min(max_workers, math.ceil(n_urls / pages_per_worker)))


def _retry_after(r: httpx.Response, attempt: int) -> float:
    """Seconds to pause after a 429/503: Retry-After when given in seconds, else 2, 4, 8…"""
    try:
        return min(float(r.headers["Retry-After"]), _MAX_BACKOFF)
    except (KeyError, ValueError):
        return min(2.0 * 2 ** attempt, _MAX_BACKOFF)


class _Throttle:
    """Shared pause so one 429/503 slows every worker, not just the one that hit it."""

    def __init__(self) -> None:
        self.resume_at = 0.0

    async def wait(self) -> None:
        # Loop: another worker may push resume_at further out while we sleep.
        while (delay := self.resume_at - time.monotonic()) > 0:
            await asyncio.sleep(delay)

    def back_off(self, seconds: float) -> None:
        self.resume_at = max(self.resume_at, time.monotonic() + seconds)


async def _fetch_page(client: httpx.AsyncClient, url: str, throttle: _Throttle) -> str | None:
    """Fetch one page, backing off and retrying on 429/503; None on any other failure."""
    for attempt in range(_MAX_RETRIES + 1):
        await throttle.wait()
        try:
            r = await client.get(url, timeout=15, follow_redirects=True)
        except Exception:
            log.debug("sitemap page fetch failed for url=%s", url, exc_info=True)
            return None
        if r.status_code in (429, 503) and attempt < _MAX_RETRIES:
            pause = _retry_after(r, attempt)
            log.info("HTTP %d from %s — pausing all crawl workers %.1fs", r.status_code, url, pause)
            throttle.back_off(pause)
            continue
        if r.is_success:
            return r.text
        log.debug("sitemap page fetch got HTTP %d for url=%s", r.status_code, url)
        return None
    return None


def _collect_meta(soup: BeautifulSoup) -> dict[str, str]:
    """
    Return a flat dict of every meta tag's name/property → content value.

    Both ``property`` (OG/Twitter) and ``name`` attributes are captured under
    their respective key so callers can look up either without branching.
    """
    result: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = tag.get("property") or tag.get("name")
        val = tag.get("content")
        if key and val:
            result[key] = val.strip()
    return result


def _best_title(soup: BeautifulSoup, meta_all: dict[str, str], url: str) -> str:
    """
    Prefer og:title (publisher-curated) over the HTML <title> element.

    Falls back to the first <h1>, then the bare URL when nothing is found.
    """
    if meta_all.get("og:title"):
        return meta_all["og:title"]
    title_tag = soup.find("title") or soup.find("h1")
    return title_tag.get_text(strip=True) if title_tag else url


def _best_description(meta_all: dict[str, str]) -> str:
    """
    Return og:description when present; otherwise fall back to meta name=description.

    Both tags are often identical — checking og: first avoids prepending the same
    text twice when both exist.
    """
    return meta_all.get("og:description") or meta_all.get("description") or ""


def _extract_content(soup: BeautifulSoup, cfg: SitemapConnectorConfig) -> str:
    """Return the best available body text from a parsed page."""
    content_el = None
    if cfg.content_selector:
        content_el = soup.select_one(cfg.content_selector)
    if not content_el:
        for sel in _CONTENT_SELECTORS:
            content_el = soup.select_one(sel)
            if content_el:
                break
    return content_el.get_text(separator=" ", strip=True) if content_el else ""


def _extract_page(html: str, url: str, cfg: SitemapConnectorConfig) -> dict | None:
    """
    Parse an HTML page into an ISP document dict.

    Returns None when no meaningful content could be extracted so the caller
    can silently skip the page.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    meta_all = _collect_meta(soup)
    title = _best_title(soup, meta_all, url)
    meta_desc = _best_description(meta_all)

    content = _extract_content(soup, cfg)

    # Prepend the curated meta description so it anchors the embedding even when
    # the main body is long and the summary text would otherwise be diluted.
    if meta_desc:
        content = meta_desc + " " + content
    content = " ".join(content.split())[:4000]

    if not content:
        return None

    # Promote the meta tags named in cfg.meta_fields to top-level document fields
    # so they are stored as first-class Typesense fields (filterable, facetable).
    extra: dict[str, str] = {
        field_name: meta_all[meta_key]
        for meta_key, field_name in cfg.meta_fields.items()
        if meta_key in meta_all
    }

    return {
        "id": hashlib.md5(url.encode()).hexdigest()[:16],
        "title": title[:200],
        "content": content,
        "url": url,
        "source_type": "sitemap",
        "indexed_at": int(time.time()),
        "metadata": json.dumps(meta_all),
        **extra,
    }


async def crawl_batches(
    cfg: SitemapConnectorConfig,
    on_batch: Callable[[list[dict]], Awaitable[None]],
    batch_size: int = 500,
    on_progress: Callable[[int, int], None] | None = None,
    ext_records: list[dict] | None = None,
) -> int:
    """
    Crawl a sitemap with parallel workers, handing pages to ``on_batch`` as they arrive.

    Args:
        cfg: Sitemap connector configuration (url, max_pages, filters, selector,
            meta_fields, pages_per_worker, max_workers).
        on_batch: Awaited with each batch of up to ``batch_size`` documents
            while crawling continues.  Calls never overlap, so the callback can
            embed and upsert without its own locking.
        batch_size: Documents per ``on_batch`` call.
        on_progress: Optional callback invoked after each page with
            ``(pages_done, total_pages)``.
        ext_records: Extension records to run the ``filterUrl()`` hook against.
            Passed in by the ingest task so filtering happens before the
            ``max_pages`` cap, making the cap apply to *post-filter* URLs rather
            than pre-filter ones.

    Returns:
        Number of URLs crawled (pages that returned no content are not passed
        to ``on_batch`` but still count here).

    Raises:
        ValueError: If the sitemap itself cannot be fetched.
    """
    ua = cfg.user_agent.strip() if cfg.user_agent else "ISP-Crawler/1.0"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                  headers={"User-Agent": ua}) as client:
        urls = list(dict.fromkeys(await _collect_page_urls(client, cfg.url)))

        if cfg.include_patterns:
            urls = [u for u in urls if any(p in u for p in cfg.include_patterns)]
        if cfg.exclude_patterns:
            urls = [u for u in urls if not any(p in u for p in cfg.exclude_patterns)]

        # filterUrl() runs before the cap so extensions can drop irrelevant URLs
        # without the cap being wasted on pages we would never have indexed.
        if ext_records:
            # Lazy import avoids a module-level circular-ish dependency; extension_runner
            # is infra, sitemap_connector is infra, but keeping the import local makes
            # the dependency explicit and easy to spot during review.
            from isp.services.infra.extension_runner import filter_urls
            urls = await filter_urls(urls, ext_records)

        urls = urls[:cfg.max_pages]
        n_workers = worker_count(len(urls), cfg.pages_per_worker, cfg.max_workers)
        delay = _POLITE_DELAY
        crawl_delay = await _robots_crawl_delay(client, cfg.url, ua)
        if crawl_delay:
            # robots.txt rate-limits the whole site, so honour it with a single worker.
            n_workers, delay = 1, max(crawl_delay, _POLITE_DELAY)
            log.info("robots.txt Crawl-delay %.1fs for %s — crawling with 1 worker", crawl_delay, cfg.url)
        log.info("Sitemap crawl: %d pages, %d workers (%s)", len(urls), n_workers, cfg.url)

        # "spawn" so the children don't inherit the API's threads, sockets, or
        # event loop via fork; start-up cost is paid once per crawl.
        parse_pool = ProcessPoolExecutor(max_workers=parse_process_count(n_workers),
                                         mp_context=multiprocessing.get_context("spawn"))
        loop = asyncio.get_running_loop()

        queue: asyncio.Queue[str] = asyncio.Queue()
        for url in urls:
            queue.put_nowait(url)
        throttle = _Throttle()
        buffer: list[dict] = []
        flush_lock = asyncio.Lock()
        failed = False
        done = 0

        async def flush(force: bool = False) -> None:
            nonlocal failed
            # Lock keeps on_batch calls sequential; other workers keep crawling meanwhile.
            async with flush_lock:
                # A worker queued on the lock behind a failed batch must not start
                # another one before the TaskGroup's cancellation reaches it.
                while not failed and (len(buffer) >= batch_size or (force and buffer)):
                    batch = buffer[:batch_size]
                    del buffer[:batch_size]
                    try:
                        await on_batch(batch)
                    except BaseException:
                        failed = True
                        raise

        async def worker() -> None:
            nonlocal done
            while True:
                try:
                    url = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                html = await _fetch_page(client, url, throttle)
                if html:
                    doc = await loop.run_in_executor(parse_pool, _extract_page, html, url, cfg)
                    if doc:
                        buffer.append(doc)
                done += 1
                if on_progress:
                    on_progress(done, len(urls))
                if len(buffer) >= batch_size:
                    await flush()
                await asyncio.sleep(delay)   # polite crawl delay

        # TaskGroup (not gather) so one failing worker cancels the rest before we
        # return: otherwise survivors keep writing after the caller has released
        # its job lock.  Re-raise the original error so job status stays readable.
        try:
            async with asyncio.TaskGroup() as tg:
                for _ in range(n_workers):
                    tg.create_task(worker())
        except ExceptionGroup as eg:
            raise eg.exceptions[0] from eg
        finally:
            # Never leave parser processes behind; queued parses are dropped on failure.
            parse_pool.shutdown(wait=True, cancel_futures=True)
        await flush(force=True)

    return len(urls)


async def crawl(
    cfg: SitemapConnectorConfig,
    on_progress: Callable[[int, int], None] | None = None,
    ext_records: list[dict] | None = None,
) -> list[dict]:
    """
    Crawl a sitemap and return every extracted page as an ISP document dict.

    Convenience wrapper over ``crawl_batches`` for callers that want the whole
    result at once; large sitemaps should use ``crawl_batches`` to stay
    memory-bounded.  Pages that returned no content are silently excluded.

    Raises:
        ValueError: If the sitemap itself cannot be fetched.
    """
    docs: list[dict] = []

    async def collect(batch: list[dict]) -> None:
        docs.extend(batch)

    await crawl_batches(cfg, collect, on_progress=on_progress, ext_records=ext_records)
    return docs
