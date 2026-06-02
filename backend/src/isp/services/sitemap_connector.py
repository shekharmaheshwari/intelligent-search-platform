from __future__ import annotations
import asyncio, hashlib, time
from typing import AsyncIterator
import httpx
from bs4 import BeautifulSoup
from isp.models import SitemapConnectorConfig

_CONTENT_SELECTORS = ["article", "main", "[role='main']", ".content", "#content", ".post-content", "body"]


async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        r.raise_for_status()
        return r.text
    except Exception:
        return None


def _parse_sitemap_urls(xml: str) -> list[str]:
    soup = BeautifulSoup(xml, "lxml-xml")
    return [loc.get_text(strip=True) for loc in soup.find_all("loc")]


def _extract_page(html: str, url: str, cfg: SitemapConnectorConfig) -> dict | None:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    title_tag = soup.find("title") or soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else url

    content_el = None
    if cfg.content_selector:
        content_el = soup.select_one(cfg.content_selector)
    if not content_el:
        for sel in _CONTENT_SELECTORS:
            content_el = soup.select_one(sel)
            if content_el:
                break

    content = content_el.get_text(separator=" ", strip=True) if content_el else ""
    content = " ".join(content.split())[:4000]   # cap at 4 000 chars

    if not content:
        return None

    return {
        "id": hashlib.md5(url.encode()).hexdigest()[:16],
        "title": title[:200],
        "content": content,
        "url": url,
        "source_type": "sitemap",
        "indexed_at": int(time.time()),
    }


async def crawl(
    cfg: SitemapConnectorConfig,
    on_progress: "Callable[[int, int], None] | None" = None,
) -> list[dict]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                  headers={"User-Agent": "ISP-Crawler/1.0"}) as client:
        sitemap_xml = await _fetch(client, cfg.url)
        if not sitemap_xml:
            raise ValueError(f"Could not fetch sitemap: {cfg.url}")

        urls = _parse_sitemap_urls(sitemap_xml)

        if cfg.include_pattern:
            urls = [u for u in urls if cfg.include_pattern in u]
        if cfg.exclude_pattern:
            urls = [u for u in urls if cfg.exclude_pattern not in u]

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
