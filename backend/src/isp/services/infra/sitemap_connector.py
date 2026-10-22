# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Sitemap crawler: fetch a sitemap XML, extract page URLs, and scrape each page.

Crawl strategy:
1. Fetch the sitemap XML from ``cfg.url`` and parse all ``<loc>`` entries.
2. Apply include/exclude substring list filters to the URL list.
3. Run the ``filterUrl()`` JS extension hook to drop unwanted URLs before fetching.
4. Truncate to ``cfg.max_pages`` (max 10 000, default 50).
5. Fetch each page with a 0.1 s polite delay between requests.
6. Strip navigation/chrome elements (script, style, nav, footer, header, aside)
   then extract content from the configured selector or a fallback cascade.

OG tags (og:title, og:description) are preferred over plain <title> / meta name tags
so publisher-curated summaries lead the embedded text.  All meta tags are captured into
a ``metadata`` JSON field; ``cfg.meta_fields`` promotes selected ones to top-level fields.

Content is capped at 4 000 characters to keep Typesense document sizes reasonable
and avoid hitting embedding token limits.

Page IDs are MD5 hashes of the URL, truncated to 16 hex chars.  This means
re-crawling the same URL overwrites the existing document (upsert behaviour).
"""
from __future__ import annotations
import asyncio, hashlib, json, logging, time
from typing import Callable
import httpx
from bs4 import BeautifulSoup
from isp.models import SitemapConnectorConfig

log = logging.getLogger(__name__)

# Ordered fallback list of CSS selectors used when the user has not configured
# a custom content_selector.  The first selector that matches a non-empty element wins.
_CONTENT_SELECTORS = ["article", "main", "[role='main']", ".content", "#content", ".post-content", "body"]


async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch a URL and return the response text, or None on any error."""
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        r.raise_for_status()
        return r.text
    except Exception:
        # Caller silently excludes pages that return no content — log at debug
        # so a systematic failure (auth wall, rate limit) is distinguishable
        # from a handful of genuinely broken links.
        log.debug("sitemap _fetch failed for url=%s", url, exc_info=True)
        return None


def _parse_sitemap_urls(xml: str) -> list[str]:
    """Extract all ``<loc>`` URLs from a sitemap XML string."""
    soup = BeautifulSoup(xml, "lxml-xml")
    return [loc.get_text(strip=True) for loc in soup.find_all("loc")]


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


async def crawl(
    cfg: SitemapConnectorConfig,
    on_progress: Callable[[int, int], None] | None = None,
    ext_records: list[dict] | None = None,
) -> list[dict]:
    """
    Crawl a sitemap and return a list of ISP document dicts.

    Args:
        cfg: Sitemap connector configuration (url, max_pages, filters, selector,
            meta_fields).
        on_progress: Optional callback invoked after each page with
            ``(pages_done, total_pages)`` — used by the rebuild panel to show
            live progress in the UI.
        ext_records: Extension records to run the ``filterUrl()`` hook against.
            Passed in by the ingest task so filtering happens before the
            ``max_pages`` cap, making the cap apply to *post-filter* URLs rather
            than pre-filter ones.

    Returns:
        List of document dicts ready for embedding and upsert.  Pages that
        returned no content are silently excluded.

    Raises:
        ValueError: If the sitemap itself cannot be fetched.
    """
    ua = cfg.user_agent.strip() if cfg.user_agent else "ISP-Crawler/1.0"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                  headers={"User-Agent": ua}) as client:
        sitemap_xml = await _fetch(client, cfg.url)
        if not sitemap_xml:
            raise ValueError(f"Could not fetch sitemap: {cfg.url}")

        urls = _parse_sitemap_urls(sitemap_xml)

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
        docs: list[dict] = []

        for i, url in enumerate(urls):
            html = await _fetch(client, url)
            if html:
                doc = _extract_page(html, url, cfg)
                if doc:
                    docs.append(doc)
            if on_progress:
                on_progress(i + 1, len(urls))
            await asyncio.sleep(0.1)   # polite crawl delay

    return docs
