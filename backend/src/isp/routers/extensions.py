from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from isp.services import extension_service

router = APIRouter(tags=["extensions"])

_SAMPLE_CODE = """\
function transform(doc) {
  // Example: rebuild the URL from a slug field
  // var slug = (doc.slug || doc.title || '').toLowerCase().replace(/\\s+/g, '-');
  // doc.url = 'https://example.com/products/' + slug;
  return doc;
}
"""


class ExtensionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    code: str = Field(default=_SAMPLE_CODE, description="JavaScript. Must define `function transform(doc) { ... return doc; }`")


class ExtensionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    code: Optional[str] = None


class IndexExtensionAssign(BaseModel):
    extension_id: str
    run_order: int = 0
    enabled: bool = True


# ── Global extension library ──────────────────────────────────────────────────

@router.get("/extensions", summary="List all extensions")
async def list_extensions():
    """Return every JS transform extension stored in the database, newest first."""
    return await extension_service.list_extensions()


@router.post("/extensions", status_code=status.HTTP_201_CREATED, summary="Create an extension")
async def create_extension(body: ExtensionCreate):
    """
    Store a new JavaScript V8 transform extension.

    The code **must** define a top-level `transform` function:

    ```js
    function transform(doc) {
      // doc is a plain object with all document fields
      doc.url = 'https://example.com/' + doc.slug;
      return doc;          // must return the (modified) doc
    }
    ```

    The extension runs inside a PyMiniRacer V8 isolate with no access to the
    filesystem, network, or Node.js APIs — only plain JS built-ins.
    """
    return await extension_service.create_extension(body.name, body.description, body.code)


@router.get("/extensions/{ext_id}", summary="Get an extension")
async def get_extension(ext_id: str):
    ext = await extension_service.get_extension(ext_id)
    if not ext:
        raise HTTPException(404, "Extension not found")
    return ext


@router.put("/extensions/{ext_id}", summary="Update an extension")
async def update_extension(ext_id: str, body: ExtensionUpdate):
    """Update name, description, and/or code. Omit fields to leave them unchanged."""
    ext = await extension_service.update_extension(ext_id, body.name, body.description, body.code)
    if not ext:
        raise HTTPException(404, "Extension not found")
    return ext


@router.delete("/extensions/{ext_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an extension")
async def delete_extension(ext_id: str):
    """
    Permanently delete an extension. Any index that had this extension assigned
    will automatically lose the assignment (CASCADE delete).
    """
    ok = await extension_service.delete_extension(ext_id)
    if not ok:
        raise HTTPException(404, "Extension not found")


# ── Per-index extension assignment ───────────────────────────────────────────

@router.get("/indexes/{idx_id}/extensions", summary="List extensions assigned to an index")
async def list_index_extensions(idx_id: str):
    """Return extensions assigned to this index, sorted by run_order (execution sequence)."""
    return await extension_service.list_index_extensions(idx_id)


@router.post(
    "/indexes/{idx_id}/extensions",
    status_code=status.HTTP_201_CREATED,
    summary="Assign an extension to an index",
)
async def assign_extension(idx_id: str, body: IndexExtensionAssign):
    """
    Assign an extension to an index with an explicit `run_order` (lower = runs first).
    If already assigned, updates `run_order` and `enabled` in place.
    Extensions are applied during the next ingest or rebuild.
    """
    await extension_service.assign_extension(idx_id, body.extension_id, body.run_order, body.enabled)
    return {"ok": True}


@router.delete(
    "/indexes/{idx_id}/extensions/{ext_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unassign an extension from an index",
)
async def unassign_extension(idx_id: str, ext_id: str):
    await extension_service.unassign_extension(idx_id, ext_id)
