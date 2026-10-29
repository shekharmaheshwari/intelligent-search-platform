# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

import asyncio
import gzip

import httpx
import pytest

from isp.models import SitemapConnectorConfig
from isp.services.infra import sitemap_connector as sc

ROOT = "https://site.test/sitemap.xml"


def _urlset(urls):
    locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'


def _index(children):
    locs = "".join(f"<sitemap><loc>{c}</loc></sitemap>" for c in children)
    return f'<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</sitemapindex>'


def _page(n):
    return f"<html><head><title>Page {n}</title></head><body><main>Body text {n}</main></body></html>"


class FakeSite:
    """httpx MockTransport handler that records peak concurrent page requests."""

    def __init__(self, routes, page_latency=0.01, robots=None, fail_first=()):
        self.routes = routes
        self.page_latency = page_latency
        self.robots = robots
        self.fail_first = set(fail_first)   # page URLs answered 429 once
        self.in_flight = 0
        self.peak = 0
        self.hits: dict[str, int] = {}

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.hits[url] = self.hits.get(url, 0) + 1
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text=self.robots) if self.robots else httpx.Response(404)
        if url in self.routes:
            body = self.routes[url]
            return httpx.Response(200, content=body if isinstance(body, bytes) else body.encode())
        if "/page/" in url:
            if url in self.fail_first:
                self.fail_first.discard(url)
                return httpx.Response(429, headers={"Retry-After": "0"})
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            await asyncio.sleep(self.page_latency)
            self.in_flight -= 1
            return httpx.Response(200, text=_page(url.rsplit("/", 1)[-1]))
        return httpx.Response(404)


@pytest.fixture
def use_site(monkeypatch):
    """Route the crawler's AsyncClient through a FakeSite; no real network, no polite delay."""
    monkeypatch.setattr(sc, "_POLITE_DELAY", 0.0)
    real_client = httpx.AsyncClient

    def install(site: FakeSite) -> FakeSite:
        monkeypatch.setattr(sc.httpx, "AsyncClient",
                            lambda **kw: real_client(transport=httpx.MockTransport(site), **kw))
        return site

    return install


def _pages(n):
    return [f"https://site.test/page/{i}" for i in range(n)]


@pytest.mark.parametrize("n, per, cap, expected", [
    (0, 1000, 8, 1), (500, 1000, 8, 1), (1000, 1000, 8, 1), (1001, 1000, 8, 2),
    (50_000, 1000, 8, 8), (50_000, 1000, 16, 16), (100_000, 1000, 32, 32), (10, 2, 8, 5),
])
def test_worker_count(n, per, cap, expected):
    assert sc.worker_count(n, per, cap) == expected


async def test_sitemap_index_with_gzip_child_is_followed(use_site):
    pages = _pages(6)
    site = use_site(FakeSite({
        ROOT: _index(["https://site.test/a.xml", "https://site.test/b.xml.gz"]),
        "https://site.test/a.xml": _urlset(pages[:3]),
        "https://site.test/b.xml.gz": gzip.compress(_urlset(pages[3:] + pages[:1]).encode()),  # dup of page 0
    }))
    cfg = SitemapConnectorConfig(url=ROOT, max_pages=100)
    docs = await sc.crawl(cfg)
    assert sorted(d["url"] for d in docs) == sorted(pages)          # all 6, duplicate dropped
    assert site.hits["https://site.test/page/0"] == 1


async def test_batches_and_worker_limit(use_site):
    site = use_site(FakeSite({ROOT: _urlset(_pages(40))}, page_latency=0.02))
    cfg = SitemapConnectorConfig(url=ROOT, max_pages=40, pages_per_worker=10, max_workers=3)
    batches: list[int] = []

    async def on_batch(batch):
        batches.append(len(batch))

    crawled = await sc.crawl_batches(cfg, on_batch, batch_size=15)
    assert crawled == 40
    assert sum(batches) == 40 and max(batches) <= 15
    assert 1 < site.peak <= 3                                       # parallel, but capped


async def test_429_is_retried_after_backoff(use_site):
    pages = _pages(3)
    site = use_site(FakeSite({ROOT: _urlset(pages)}, fail_first=[pages[1]]))
    docs = await sc.crawl(SitemapConnectorConfig(url=ROOT, max_pages=10))
    assert len(docs) == 3
    assert site.hits[pages[1]] == 2


async def test_robots_crawl_delay_forces_single_worker(use_site):
    # Fractional delay is rounded up to 1s, so keep the page count small.
    site = use_site(FakeSite({ROOT: _urlset(_pages(2))}, robots="User-agent: *\nCrawl-delay: 0.5\n"))
    cfg = SitemapConnectorConfig(url=ROOT, max_pages=10, pages_per_worker=1, max_workers=8)
    docs = await sc.crawl(cfg)
    assert len(docs) == 2
    assert site.peak == 1


@pytest.mark.parametrize("line, expected", [
    ("Crawl-delay: 0.5", "Crawl-delay: 1"),
    ("crawl-delay: 2.1 # be nice", "crawl-delay: 3"),
    ("Crawl-delay: 10", "Crawl-delay: 10"),
    ("Crawl-delay: soon", "Crawl-delay: soon"),
    ("Disallow: /private", "Disallow: /private"),
])
def test_round_up_crawl_delays(line, expected):
    assert sc._round_up_crawl_delays([line]) == [expected]


async def test_unreachable_root_sitemap_raises(use_site):
    use_site(FakeSite({}))
    with pytest.raises(ValueError, match="Could not fetch sitemap"):
        await sc.crawl(SitemapConnectorConfig(url=ROOT))


async def test_unreachable_child_sitemap_is_skipped(use_site):
    use_site(FakeSite({ROOT: _index(["https://site.test/missing.xml", "https://site.test/ok.xml"]),
                       "https://site.test/ok.xml": _urlset(_pages(2))}))
    docs = await sc.crawl(SitemapConnectorConfig(url=ROOT))
    assert len(docs) == 2


async def test_failing_batch_stops_every_worker(use_site):
    """After crawl_batches raises, no worker may keep fetching or calling on_batch."""
    # batch_size=1: any page a surviving worker finishes would call on_batch again.
    site = use_site(FakeSite({ROOT: _urlset(_pages(60))}, page_latency=0.05))
    cfg = SitemapConnectorConfig(url=ROOT, max_pages=60, pages_per_worker=10, max_workers=4)
    calls = 0

    async def on_batch(batch):
        nonlocal calls
        calls += 1
        raise RuntimeError("embedding provider down")

    with pytest.raises(RuntimeError, match="embedding provider down"):   # original error, not a group
        await sc.crawl_batches(cfg, on_batch, batch_size=1)
    fetched = sum(n for u, n in site.hits.items() if "/page/" in u)
    await asyncio.sleep(0.2)                                              # give stragglers a chance
    assert calls == 1
    assert sum(n for u, n in site.hits.items() if "/page/" in u) == fetched
    assert site.in_flight == 0


async def test_throttle_wait_honours_extension_made_while_sleeping():
    import time
    throttle = sc._Throttle()
    throttle.back_off(0.05)

    async def extend():
        await asyncio.sleep(0.02)
        throttle.back_off(0.15)          # another worker hit a fresh 429

    start = time.monotonic()
    await asyncio.gather(throttle.wait(), extend())
    assert time.monotonic() - start >= 0.15


@pytest.mark.parametrize("workers, cpus, expected", [(1, 6, 1), (8, 6, 6), (4, 6, 4), (30, 6, 6), (8, None, 1)])
def test_parse_process_count(monkeypatch, workers, cpus, expected):
    monkeypatch.setattr(sc.os, "cpu_count", lambda: cpus)
    assert sc.parse_process_count(workers) == expected
