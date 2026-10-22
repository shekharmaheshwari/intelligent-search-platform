# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Distributed job lock for ISP ingest/rebuild tasks.

Prevents multiple ISP instances from running the same rebuild concurrently.
Uses Redis SET NX as the mutex.  Instance identity is the container hostname
(stable across process restarts within the same Docker container, unique
across containers in a multi-instance deployment).

Key:   isp:job:{idx_id}
Value: "{hostname}:{uuid4()}"

The lock is acquired INSIDE the background task (not in the HTTP handler).
This means a worker crash between enqueue and task-start never orphans a lock.

Usage pattern in a background task:
    token = await job_lock.acquire(idx_id)
    if token is None:
        # another instance won; surface an error so the poller stops
        return
    hb = asyncio.create_task(job_lock.heartbeat(idx_id, token))
    try:
        ... do work ...
    finally:
        hb.cancel()
        await job_lock.release(idx_id, token)
"""
from __future__ import annotations
import asyncio, logging, socket, uuid
import redis.asyncio as aioredis
from isp.config import settings

log = logging.getLogger(__name__)

# Stable within one Docker container across process restarts; unique across containers.
_HOSTNAME   = socket.gethostname()
_KEY_PREFIX = "isp:job:"
_INITIAL_TTL = 600   # 10 minutes — short enough to unblock quickly after a hard crash
_HB_INTERVAL = 300   # extend TTL every 5 minutes
_HB_EXTEND   = 600   # each heartbeat resets TTL to 10 minutes

# Atomically delete the key only if the stored value matches our token.
# Prevents instance A from releasing a lock that instance B re-acquired after
# A's TTL expired.
_LUA_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1
end
return 0
"""

# Atomically extend TTL only if we still own the lock.
_LUA_EXTEND = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
    return 1
end
return 0
"""


async def _connect() -> aioredis.Redis | None:
    """Return a live Redis connection or None if Redis is unreachable."""
    try:
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=1)
        await r.ping()
        return r
    except Exception:
        return None


def _make_token() -> str:
    """Build a lock token that encodes this container and a unique nonce."""
    return f"{_HOSTNAME}:{uuid.uuid4()}"


async def acquire(idx_id: str) -> str | None:
    """
    Try to acquire the rebuild lock for idx_id.

    Returns an opaque token string on success (pass it to release / heartbeat),
    or None if another instance already holds the lock.

    Degrades gracefully when Redis is unreachable: returns a token so the job
    proceeds — single-instance deployments are unaffected.
    """
    r = await _connect()
    if r is None:
        log.warning("job_lock: Redis unreachable — skipping distributed lock for %s", idx_id)
        return _make_token()

    token = _make_token()
    key = f"{_KEY_PREFIX}{idx_id}"
    try:
        acquired = await r.set(key, token, nx=True, ex=_INITIAL_TTL)
        if acquired:
            log.debug("job_lock: acquired for %s", idx_id)
            return token
        log.info("job_lock: busy for %s — another instance is running this job", idx_id)
        return None
    except Exception as exc:
        log.warning("job_lock: acquire error for %s: %s — proceeding without lock", idx_id, exc)
        return _make_token()
    finally:
        await r.aclose()


async def release(idx_id: str, token: str) -> None:
    """Release the lock only if this instance still owns it (Lua compare-and-delete)."""
    r = await _connect()
    if r is None:
        return
    key = f"{_KEY_PREFIX}{idx_id}"
    try:
        await r.eval(_LUA_RELEASE, 1, key, token)
        log.debug("job_lock: released for %s", idx_id)
    except Exception as exc:
        log.warning("job_lock: release error for %s: %s", idx_id, exc)
    finally:
        await r.aclose()


async def heartbeat(idx_id: str, token: str) -> None:
    """
    Extend the lock TTL every ``_HB_INTERVAL`` seconds until cancelled.

    Run as an asyncio task alongside the background job:
        hb = asyncio.create_task(job_lock.heartbeat(idx_id, token))
        try:
            ...
        finally:
            hb.cancel()
    """
    key = f"{_KEY_PREFIX}{idx_id}"
    try:
        while True:
            await asyncio.sleep(_HB_INTERVAL)
            r = await _connect()
            if r is None:
                continue
            try:
                await r.eval(_LUA_EXTEND, 1, key, token, str(_HB_EXTEND))
                log.debug("job_lock: extended TTL for %s", idx_id)
            except Exception as exc:
                log.warning("job_lock: heartbeat error for %s: %s", idx_id, exc)
            finally:
                await r.aclose()
    except asyncio.CancelledError:
        pass


async def release_stale_for_instance() -> None:
    """
    Delete lock keys left by a previous run of this container.

    Called once at startup.  Scans ``isp:job:*`` and deletes any key whose
    value starts with our hostname.  SCAN is acceptable here — it runs once
    at boot and there are normally zero job keys.
    """
    r = await _connect()
    if r is None:
        return
    prefix = f"{_HOSTNAME}:"
    try:
        async for raw_key in r.scan_iter(f"{_KEY_PREFIX}*"):
            val = await r.get(raw_key)
            if val and val.decode(errors="replace").startswith(prefix):
                key_str = raw_key.decode(errors="replace") if isinstance(raw_key, bytes) else raw_key
                idx_id = key_str.removeprefix(_KEY_PREFIX)
                await r.delete(raw_key)
                log.info("job_lock: released stale lock for index %s from prior run", idx_id)
    except Exception as exc:
        log.warning("job_lock: startup cleanup error: %s", exc)
    finally:
        await r.aclose()
