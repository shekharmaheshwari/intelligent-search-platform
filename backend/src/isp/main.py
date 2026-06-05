from __future__ import annotations
import logging
import re
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from isp.routers import indexes, search, content, catalog, extensions
from isp.services.database import init_db
from isp.services import typesense_client as ts
from isp.config import settings

log = logging.getLogger(__name__)

_TMOBILE_EXT_NAME = "T-Mobile Catalog Mapper"   # used by discover endpoint for auto-assign

_CATALOG_VEC_DIM = 1024


def _ensure_catalog_collection() -> None:
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


def _parse_js_header(code: str) -> tuple[str, str]:
    """Extract @name and @description from JS comment header. Returns (name, description)."""
    name = desc = ""
    for line in code.splitlines():
        line = line.strip()
        if not line.startswith("//"):
            break
        m = re.match(r"//\s*@name\s+(.+)", line)
        if m:
            name = m.group(1).strip()
        m = re.match(r"//\s*@description\s+(.+)", line)
        if m:
            desc = m.group(1).strip()
    return name, desc


async def _seed_extensions_from_files() -> None:
    """
    Scan settings.extensions_dir for *.js files, upsert each into the DB, and
    auto-assign to any existing external (catalog write-back) indexes.

    This makes the repo the single source of truth for extension code — committing
    a new .js file is all it takes to deploy a new extension.
    """
    from isp.services import extension_service, index_service

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
        name, desc = _parse_js_header(code)
        if not name:
            name = js_path.stem.replace("_", " ").title()

        ext = await extension_service.upsert_extension_by_name(name, desc, code)
        log.info("Seeded extension '%s' from %s", name, js_path.name)

        for idx in external_indexes:
            await extension_service.assign_extension_if_new(idx.id, ext["id"], run_order=0)
            log.debug("Auto-assigned '%s' to index '%s'", name, idx.name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    _ensure_catalog_collection()
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
        "- **Catalog** — unified product collection shared by all T-Mobile catalog sources; "
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

app.include_router(indexes.router, prefix="/v1")
app.include_router(search.router, prefix="/v1")
app.include_router(content.router, prefix="/v1")
app.include_router(catalog.router, prefix="/v1")
app.include_router(extensions.router, prefix="/v1")


@app.get("/health")
def health():
    return {"ok": True}
