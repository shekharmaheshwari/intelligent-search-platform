# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Anonymous usage telemetry — one ping on startup, then one heartbeat per day.

What is sent (and nothing else): a random install id, the ISP version, OS /
architecture / Python version, the Typesense server version, the embedding and
chat *provider types*, and a bucketed index count.  Never documents, queries,
index names, URLs, hostnames, keys, or emails.  See README → Telemetry.

Disabled when any of these is true:
- ``TELEMETRY_ENABLED=false``
- ``DO_NOT_TRACK`` is set to a truthy value (https://consoledonottrack.com)
- running under CI (``CI`` env var) or pytest
- no endpoint is configured (``TELEMETRY_URL`` empty and no built-in default)

Every network call is fire-and-forget with a short timeout; telemetry can never
block startup or raise into the application.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import sys
import uuid

import httpx

from isp.config import settings

log = logging.getLogger(__name__)

# Endpoint every install reports to unless TELEMETRY_URL overrides it.
# Collector source: telemetry-worker/ (Cloudflare Worker + D1).
DEFAULT_TELEMETRY_URL = "https://isp-telemetry.shekhar1maheshwari.workers.dev/ping"

_INSTALL_ID_KEY = "telemetry_install_id"
_HEARTBEAT_SECONDS = 24 * 60 * 60
_TIMEOUT_SECONDS = 2.0
_TRUTHY = {"1", "true", "yes", "on"}

_heartbeat_task: asyncio.Task | None = None


def _endpoint() -> str:
    return settings.telemetry_url or DEFAULT_TELEMETRY_URL


def is_enabled() -> bool:
    """True only when telemetry is allowed and has somewhere to send to."""
    if not settings.telemetry_enabled:
        return False
    # Via Settings so DO_NOT_TRACK in backend/.env counts too, not only the process env.
    if settings.do_not_track.strip().lower() in _TRUTHY:
        return False
    if _in_ci_or_tests():
        return False
    return bool(_endpoint())


def _in_ci_or_tests() -> bool:
    return bool(os.environ.get("CI")) or "pytest" in sys.modules


def index_count_bucket(n: int) -> str:
    """Coarse range so the exact deployment size is never reported."""
    if n == 0:
        return "0"
    if n <= 10:
        return "1-10"
    if n <= 50:
        return "11-50"
    return "50+"


async def _install_id() -> str:
    """Random UUID generated on first start and kept in ``app_settings``."""
    from isp.services.domain.settings_service import get_setting, set_setting
    existing = await get_setting(_INSTALL_ID_KEY)
    if existing:
        return existing
    new_id = str(uuid.uuid4())
    await set_setting(_INSTALL_ID_KEY, new_id)
    return new_id


def _typesense_version() -> str:
    try:
        from isp.services.infra.typesense_client import get_client
        return str(get_client().debug.retrieve().get("version", "unknown"))
    except Exception:
        return "unknown"


async def build_payload(event: str) -> dict[str, str]:
    """Assemble the exact fields documented in the README — nothing more."""
    from isp.services.domain.index_service import list_indexes
    from isp.services.infra import embedding_service
    from isp.services.infra.chat import service as chat_service

    try:
        n_indexes = len(await list_indexes())
    except Exception:
        n_indexes = 0
    return {
        "event": event,
        "install_id": await _install_id(),
        "version": _isp_version(),
        "os": platform.system().lower(),
        "arch": platform.machine().lower(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        # Sync Typesense SDK call — off the event loop so a slow server can't stall requests.
        "typesense_version": await asyncio.to_thread(_typesense_version),
        "embedding_provider": embedding_service._get_provider(embedding_service.get_current_model()),
        "chat_provider": chat_service.get_provider(),
        "index_count_bucket": index_count_bucket(n_indexes),
    }


def _isp_version() -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("intelligent-search-platform")
    except PackageNotFoundError:
        return "unknown"


async def send(event: str) -> None:
    """Send one event.  Swallows every error — telemetry must never break ISP."""
    try:
        payload = await build_payload(event)
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            await client.get(_endpoint(), params=payload)
    except Exception as exc:
        log.debug("telemetry ping failed (ignored): %s", exc)


async def _heartbeat_loop() -> None:
    await send("platform_start")
    while True:
        await asyncio.sleep(_HEARTBEAT_SECONDS)
        await send("heartbeat")


def start() -> None:
    """Called from the app lifespan.  Returns immediately; pings run in the background."""
    global _heartbeat_task
    if not is_enabled():
        log.info("Anonymous telemetry is OFF.")
        return
    log.info(
        "Anonymous telemetry is ON (install id, version, OS, provider types only). "
        "Set TELEMETRY_ENABLED=false or DO_NOT_TRACK=1 to disable. See README#telemetry."
    )
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())


def stop() -> None:
    """Cancel the heartbeat on shutdown."""
    if _heartbeat_task is not None:
        _heartbeat_task.cancel()
