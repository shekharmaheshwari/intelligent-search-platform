"""
FastAPI application entry point.

Startup sequence (lifespan):
1. ``init_db()`` — create all PostgreSQL tables (idempotent).
2. ``_ensure_catalog_collection()`` — create the ``catalog`` Typesense collection
   if it does not yet exist (first-run only).
3. ``_seed_extensions_from_files()`` — scan ``settings.extensions_dir`` for ``*.js``
   files, upsert each into the DB by name, and auto-assign to all existing external
   indexes using ``ON CONFLICT DO NOTHING`` (preserves user-configured state).

The ``_CATALOG_EXT_NAME`` constant is used by the ``discover_indexes`` endpoint to
look up the seeded extension by name so it can be assigned to newly discovered
external indexes at runtime.
"""
from __future__ import annotations
import logging
import os
import re
from pathlib import Path
from contextlib import asynccontextmanager

# Without this, the root logger defaults to WARNING and every log.info()/
# log.debug() call across the app is silently dropped — only log.error()/
# log.warning() would ever reach `docker logs`. Must run before any other
# ISP module is imported so their module-level `logging.getLogger(__name__)`
# calls inherit this configuration from the start — which is also why this
# reads LOG_LEVEL directly from the environment rather than via isp.config.settings:
# that module must not be imported before secrets bootstrap runs below, but this
# needs to run before that import too.
_LOG_LEVEL_NAME = os.environ.get("LOG_LEVEL", "INFO").upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, None)
if not isinstance(_LOG_LEVEL, int):
    logging.warning("Invalid LOG_LEVEL=%r, falling back to INFO", _LOG_LEVEL_NAME)
    _LOG_LEVEL = logging.INFO

logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Bootstrap secrets before any ISP module loads config.py
from isp.services.infra.secrets import bootstrap_secrets
bootstrap_secrets()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from isp.routers import indexes, search, content, catalog, extensions, settings as settings_router, analytics, training as training_router, chat as chat_router_module
from isp.routers import auth as auth_router_module, api_keys as api_keys_router_module
from isp.routers import agent_setup as agent_setup_router
from isp.routers.admin import router as admin_router
from isp.services.infra.database import init_db
from isp.services.domain.settings_service import load_embed_model, load_chat_config
from isp.services.infra import typesense_client as ts
from isp.config import settings

log = logging.getLogger(__name__)

_CATALOG_EXT_NAME = "Catalog Mapper"   # used by discover endpoint for auto-assign

_CATALOG_VEC_DIM = 1024


def _ensure_catalog_collection() -> None:
    """
    Create the ``catalog`` Typesense collection if it does not already exist.

    Called once on startup.  The catalog collection is shared by all external
    sources (Phones, Accessories, etc.) and by the PLP frontend.
    ISP does not drop or recreate it on restart — only schema-compatible fields are defined.
    """
    try:
        ts.get_client().collections["catalog"].retrieve()
        return
    except Exception:
        pass
    ts.get_client().collections.create({
        "name": "catalog",
        "fields": [
            {"name": "display",     "type": "string"},
            {"name": "type",        "type": "string",   "facet": True},
            {"name": "aliases",     "type": "string[]", "optional": True},
            {"name": "tags",        "type": "string[]", "facet": True, "optional": True},
            {"name": "segment",     "type": "string[]", "facet": True, "optional": True},
            {"name": "popularity",  "type": "int32"},
            {"name": "is_featured", "type": "bool",     "optional": True},
            {"name": "created_at",  "type": "int64",    "optional": True},
            {"name": "updated_at",  "type": "int64",    "optional": True},
            {"name": "vec",         "type": "float[]",  "num_dim": _CATALOG_VEC_DIM, "optional": True},
        ],
        "default_sorting_field": "popularity",
    })


def _parse_js_header(code: str) -> dict:
    """
    Extract annotation tags from a JS file's leading ``//`` comment block.

    Supported tags:
    - ``@name``        Display name for the extension.
    - ``@description`` One-line description.
    - ``@assign``      Scope for auto-assignment: ``external`` (default) or ``all``.
    - ``@run_order``   Integer execution order (default 0). Higher numbers run later.
    """
    result = {"name": "", "description": "", "assign": "external", "run_order": 0, "type": "transform"}
    for line in code.splitlines():
        line = line.strip()
        if not line.startswith("//"):
            break
        for tag, key in (("@name", "name"), ("@description", "description"), ("@assign", "assign"), ("@type", "type")):
            m = re.match(rf"//\s*{tag}\s+(.+)", line)
            if m:
                result[key] = m.group(1).strip()
        m = re.match(r"//\s*@run_order\s+(\d+)", line)
        if m:
            result["run_order"] = int(m.group(1))
    return result


async def _seed_extensions_from_files() -> None:
    """
    Scan settings.extensions_dir for *.js files, upsert each into the DB, and
    auto-assign to the appropriate indexes based on the ``@assign`` annotation.

    - ``@assign external`` (default): assign to external (catalog write-back) indexes only.
    - ``@assign all``: assign to every index, ISP-managed and external alike.
    - ``@run_order N``: set the execution order (default 0). Extensions chain in ascending order.

    This makes the repo the single source of truth for extension code — committing
    a new .js file is all it takes to deploy a new extension.
    """
    from isp.services.domain import extension_service, index_service

    ext_dir = Path(settings.extensions_dir)
    if not ext_dir.is_dir():
        log.warning("Extensions directory not found: %s — skipping seed", ext_dir)
        return

    js_files = sorted(ext_dir.glob("*.js"))
    if not js_files:
        log.info("No .js files found in %s", ext_dir)
        return

    all_indexes = await index_service.list_indexes()
    external_indexes = [idx for idx in all_indexes if idx.is_external]

    for js_path in js_files:
        code = js_path.read_text(encoding="utf-8")
        meta = _parse_js_header(code)
        name = meta["name"] or js_path.stem.replace("_", " ").title()
        desc = meta["description"]
        assign = meta["assign"]
        run_order = meta["run_order"]
        ext_type = meta["type"]

        ext = await extension_service.upsert_extension_by_name(name, desc, code, ext_type)
        log.info("Seeded extension '%s' from %s (type=%s, assign=%s, run_order=%d)",
                 name, js_path.name, ext_type, assign, run_order)

        # Pre-processor extensions are assigned manually via the Extensions tab,
        # never auto-assigned at startup — they target a specific connector's raw response.
        if ext_type == "pre_processor" or assign == "none":
            continue

        targets = all_indexes if assign == "all" else external_indexes
        for idx in targets:
            await extension_service.assign_extension_if_new(idx.id, ext["id"], run_order=run_order)
            log.debug("Auto-assigned '%s' to index '%s'", name, idx.name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager — runs startup logic before the first request
    and teardown logic (none currently) after the last.

    Startup order matters: ``init_db`` must complete before ``_seed_extensions_from_files``
    because seeding reads and writes PostgreSQL tables.
    """
    from isp.services.infra import job_lock
    from isp.services.domain.index_service import seed_slugs
    await job_lock.release_stale_for_instance()  # clear any locks left by a prior crash of this container
    await init_db()
    # Back-fill slug for any indexes that were created before the slug column was added.
    await seed_slugs()
    await load_embed_model()          # restore operator-chosen embedding model before serving requests
    await load_chat_config()          # restore operator-chosen chat provider before serving requests
    _ensure_catalog_collection()
    ts.migrate_isp_fields()
    # Provision the search_suggestions collection used by the autocomplete API.
    ts.ensure_suggestions_collection()
    await _seed_extensions_from_files()
    yield


app = FastAPI(
    title="Intelligent Search Platform",
    version="0.1.0",
    description=(
        "ISP is a self-contained search backend that indexes content from REST APIs and sitemaps "
        "into Typesense, generates 1024-dim embeddings via Voyage AI, and exposes hybrid "
        "(lexical + semantic) search.\n\n"
        "**Core concepts**\n"
        "- **Index** — a named source of content (e.g. *Phones*, *Accessories*). "
        "Each index maps to a Typesense collection.\n"
        "- **Connector** — configuration that tells ISP where to fetch data (REST endpoint or sitemap).\n"
        "- **Ingest / Rebuild** — background job that fetches, embeds, and upserts documents.\n"
        "- **Catalog** — unified product collection shared by all catalog sources; "
        "powers the PLP search UI.\n\n"
        "**Auth** — no auth required in local-dev mode. Add `X-API-Key` header in production."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.auth_enabled:
    from isp.auth.middleware import AuthMiddleware
    app.add_middleware(AuthMiddleware)

app.include_router(indexes.router, prefix="/v1")
app.include_router(search.router, prefix="/v1")
app.include_router(content.router, prefix="/v1")
app.include_router(catalog.router, prefix="/v1")
app.include_router(extensions.router, prefix="/v1")
app.include_router(settings_router.router, prefix="/v1")
app.include_router(analytics.router, prefix="/v1")
app.include_router(training_router.router, prefix="/v1")
app.include_router(admin_router, prefix="/v1/admin")
# Auth routes are always registered so /auth/login, /auth/callback etc. exist;
# the middleware only enforces guards when AUTH_ENABLED=true.
app.include_router(auth_router_module.router)
app.include_router(api_keys_router_module.router, prefix="/v1")
app.include_router(chat_router_module.router, prefix="/v1")
app.include_router(agent_setup_router.router)


_DATA_DIR = Path("/app/data")
if _DATA_DIR.is_dir():
    app.mount("/data", StaticFiles(directory=str(_DATA_DIR)), name="data")


@app.get("/health")
def health():
    """Liveness probe. Returns 200 as long as the process is running."""
    return {"ok": True}
