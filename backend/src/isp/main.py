from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from isp.routers import indexes, search, content, catalog
from isp.services.database import init_db
from isp.services import typesense_client as ts

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    _ensure_catalog_collection()
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


@app.get("/health")
def health():
    return {"ok": True}
