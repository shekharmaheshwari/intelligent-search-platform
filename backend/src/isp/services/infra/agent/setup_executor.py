# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Setup executor: takes the artifacts from claude_analyst and runs all ISP
pipeline steps to produce a live, searchable index.

Yields SSE-compatible event dicts so the router can stream progress to the UI.
Each yielded dict: { "step": str, "status": "ok"|"error", "message": str, **extra }
"""
from __future__ import annotations
import json
import time
from typing import AsyncIterator


async def run(
    artifacts: dict,
    index_name: str,
    json_path: str,
    root_key: str,
    total_items: int,
) -> AsyncIterator[dict]:
    """
    Execute the full agent setup pipeline and yield progress events.

    Steps:
      1. Create ISP index + Typesense collection
      2. Save the 3 JS extensions to DB
      3. Assign extensions to the index
      4. Configure json_file connector
      5. Apply field_config
      6. Run rebuild (ingest + embed + index)
    """
    from isp.services.domain import index_service, extension_service
    from isp.services.infra import typesense_client as ts
    from isp.services.infra.embedding_service import get_current_model
    from isp.services.infra.typesense_client import get_vec_dim

    # ── Step 1: Create index ─────────────────────────────────────────────────
    yield _ev("create_index", "running", "Creating index...")
    try:
        intent_label = artifacts.get("intent_label", "item")
        meta = await index_service.create_index(
            name=index_name,
            description=f"Created by ISP Setup Agent from {json_path}",
            intent_label=intent_label,
        )
        ts.create_collection(meta.id, vec_dim=get_vec_dim())
        effective_model = get_current_model()
        yield _ev("create_index", "ok", f"Index created — id: {meta.id}", index_id=meta.id)
    except Exception as e:
        yield _ev("create_index", "error", str(e))
        return

    index_id = meta.id

    # ── Step 2: Save extensions ──────────────────────────────────────────────
    yield _ev("save_extensions", "running", "Saving generated extensions...")
    try:
        ext_normalizer = await extension_service.upsert_extension_by_name(
            name=f"agent_normalizer_{index_id}",
            description=f"Auto-generated normalizer for {index_name}",
            code=artifacts["normalizer_js"],
            ext_type="transform",
        )
        ext_chat = await extension_service.upsert_extension_by_name(
            name=f"agent_chat_content_{index_id}",
            description=f"Auto-generated chatContent for {index_name}",
            code=artifacts["chat_content_js"],
            ext_type="transform",
        )
        ext_suggest = await extension_service.upsert_extension_by_name(
            name=f"agent_suggestions_{index_id}",
            description=f"Auto-generated suggestions for {index_name}",
            code=_build_suggestion_js(artifacts),
            ext_type="transform",
        )
        yield _ev("save_extensions", "ok", "3 extensions saved (normalizer · chatContent · suggestions)")
    except Exception as e:
        yield _ev("save_extensions", "error", str(e))
        return

    # ── Step 3: Assign extensions ────────────────────────────────────────────
    yield _ev("assign_extensions", "running", "Assigning extensions to index...")
    try:
        await extension_service.assign_extension(index_id, ext_normalizer["id"], run_order=1)
        await extension_service.assign_extension(index_id, ext_chat["id"],       run_order=2)
        await extension_service.assign_extension(index_id, ext_suggest["id"],    run_order=3)
        yield _ev("assign_extensions", "ok", "Extensions assigned (run_order 1→2→3)")
    except Exception as e:
        yield _ev("assign_extensions", "error", str(e))
        return

    # ── Step 4: Configure connector ──────────────────────────────────────────
    yield _ev("configure_connector", "running", "Configuring json_file connector...")
    try:
        connector_cfg = {
            "type":          "json_file",
            "path":          json_path,
            "root_key":      root_key,
            "id_field":      artifacts.get("id_field", "id"),
            "title_field":   artifacts.get("title_field", "title"),
            "content_field": artifacts.get("content_field", "content"),
            "url_field":     artifacts.get("url_field", "url"),
        }
        meta = await index_service.add_connector(index_id, connector_cfg)
        yield _ev("configure_connector", "ok", f"Connector set — path: {json_path}")
    except Exception as e:
        yield _ev("configure_connector", "error", str(e))
        return

    # ── Step 5: Apply field_config ───────────────────────────────────────────
    yield _ev("field_config", "running", "Applying field configuration...")
    try:
        field_config = artifacts.get("field_config", [])
        if field_config:
            meta.field_config = field_config
            await index_service.update_index(meta)
        yield _ev("field_config", "ok", f"{len(field_config)} fields configured")
    except Exception as e:
        yield _ev("field_config", "error", str(e))
        # Non-fatal — proceed with rebuild

    # ── Step 6: Rebuild (ingest + embed + index) ─────────────────────────────
    yield _ev("rebuild", "running", f"Ingesting {total_items} items, embedding, and indexing...")
    try:
        # Trigger rebuild synchronously so we can stream its completion
        await _run_rebuild_sync(index_id, connector_cfg)
        final_meta = await index_service.get_index(index_id)
        doc_count = final_meta.doc_count if final_meta else total_items
        yield _ev("rebuild", "ok", f"{doc_count} documents indexed and searchable",
                  doc_count=doc_count)
    except Exception as e:
        yield _ev("rebuild", "error", str(e))
        return

    yield _ev("done", "ok", "Index is live. Setup complete.", index_id=index_id)


async def _run_rebuild_sync(index_id: str, connector_cfg: dict) -> None:
    """Run the rebuild pipeline directly (not as a background task)."""
    from isp.services.domain import index_service, extension_service
    from isp.services.infra import typesense_client as ts, json_file_connector
    from isp.services.infra import embedding_service
    from isp.services.infra.typesense_client import get_vec_dim
    from isp.models import JsonFileConnectorConfig, IngestStatus

    meta = await index_service.get_index(index_id)
    if not meta:
        raise ValueError("Index not found")

    s = IngestStatus(index_id=index_id, state="running")
    await index_service.set_status(s)

    cfg_data = {k: v for k, v in connector_cfg.items() if k != "type"}
    jf_cfg = JsonFileConnectorConfig(**cfg_data)

    ext_records = await extension_service.list_index_extensions(index_id)
    transform_exts = [e for e in ext_records if e.get("type", "transform") == "transform"]

    from isp.routers.indexes import apply_extensions, _ensure_isp_schema, _build_embed_text

    raw_items = await json_file_connector.fetch_raw_items(jf_cfg)
    s.total = len(raw_items)
    await index_service.set_status(s)

    if transform_exts:
        docs = await apply_extensions(list(raw_items), transform_exts)
        docs = _ensure_isp_schema(docs)
        # Repair any fields the extension botched (e.g. bad .join() → "[object Object]")
        docs = [_repair_doc(docs[i], raw_items[i] if i < len(raw_items) else {})
                for i in range(len(docs))]
    else:
        docs = _ensure_isp_schema(list(raw_items))
        docs = [_flatten_objects(d) for d in docs]

    if not docs:
        raise ValueError("0 documents after parsing and applying extensions.")

    effective_model = meta.embed_model or embedding_service.get_current_model()
    raw_texts = [_build_embed_text(d) for d in docs]
    embed_idxs = [i for i, t in enumerate(raw_texts) if t]
    if embed_idxs:
        vecs = await embedding_service.embed_texts(
            [raw_texts[i] for i in embed_idxs], model=effective_model)
        for list_pos, doc_idx in enumerate(embed_idxs):
            if vecs[list_pos]:
                docs[doc_idx]["vec"] = vecs[list_pos]

    ts.drop_collection(index_id)
    if effective_model == "custom":
        from isp.routers.indexes import get_custom_config
        cfg_c = await get_custom_config()
        rebuild_dim = cfg_c["num_dim"] or get_vec_dim()
    else:
        rebuild_dim = get_vec_dim()
    ts.create_collection(index_id, vec_dim=rebuild_dim)

    ok = ts.upsert_documents(index_id, docs)
    s.indexed = ok
    s.state = "done"
    await index_service.set_status(s)

    meta.doc_count = ok
    meta.is_external = False
    meta.collection_name = f"isp_{index_id}"
    meta.connectors = [connector_cfg]
    await index_service.update_index(meta)

    from isp.services.domain import suggestion_service
    await suggestion_service.seed_suggestions_from_index(index_id)


import re as _re
_OBJ_OBJ_RE = _re.compile(r'\[object Object\]')


def _repair_doc(processed: dict, raw: dict) -> dict:
    """
    After extensions run, fix two classes of bad output:
    1. Fields still holding a dict or list-of-dicts (extension didn't touch them).
    2. Fields that became "[object Object]" strings because JS called .join()
       on an array of objects without .map() to extract values first.
    For case 2, we re-flatten from the original raw item so meaningful data
    (e.g. variant titles, image URLs) is recovered.
    Extensions that correctly handled their fields are unaffected.
    """
    doc = dict(processed)
    for key, val in list(doc.items()):
        if isinstance(val, dict) or (
            isinstance(val, list) and val and isinstance(val[0], dict)
        ):
            # Extension left nested structure — flatten it now
            doc.update(_flatten_objects({key: val}))
        elif isinstance(val, str) and _OBJ_OBJ_RE.search(val):
            # JS did a bad .join() on objects — re-flatten from the raw original
            raw_val = raw.get(key)
            if raw_val is not None:
                doc.update(_flatten_objects({key: raw_val}))
            else:
                doc[key] = ""
    return doc


def _flatten_objects(doc: dict) -> dict:
    """
    Safety net: after JS extensions run, any field still holding a dict or
    list-of-dicts would cause Typesense upsert errors. Flatten them to strings
    using the most meaningful property available.
    """
    _STRING_KEYS = ("title", "name", "src", "url", "href", "value", "label", "text")

    def _obj_to_str(obj) -> str:
        if isinstance(obj, dict):
            for k in _STRING_KEYS:
                if obj.get(k) not in (None, "", []):
                    return str(obj[k])
            # fallback: join all scalar values
            parts = [str(v) for v in obj.values() if isinstance(v, (str, int, float, bool)) and v not in ("", None)]
            return ", ".join(parts)
        return str(obj)

    flat = {}
    for key, val in doc.items():
        if isinstance(val, dict):
            flat[key] = _obj_to_str(val)
        elif isinstance(val, list):
            if not val:
                flat[key] = ""
            elif isinstance(val[0], dict):
                # Special case: images array → extract src into doc.image if not set
                if key == "images" and not doc.get("image"):
                    flat["image"] = val[0].get("src") or val[0].get("url") or ""
                flat[key] = ", ".join(_obj_to_str(item) for item in val if item)
            else:
                flat[key] = val  # array of primitives — keep
        else:
            flat[key] = val
    return flat


def _build_suggestion_js(artifacts: dict) -> str:
    """
    Build a reliable suggestion extension that always defines extractSuggestions(doc).
    Claude's output is used for the secondary brand+category combo, but the primary
    title-based suggestion is guaranteed regardless of Claude's format.
    """
    intent_label = artifacts.get("intent_label", "item")
    return f"""
function extractSuggestions(doc) {{
  var results = [];
  var title = doc.title || "";
  var category = doc.intent_label || "{intent_label}";
  if (title) {{
    results.push({{query: title.toLowerCase(), category: category}});
  }}
  var brand = doc.brand || "";
  var cat = doc.category_name || doc.category || "";
  if (brand && cat) {{
    results.push({{query: (brand + " " + cat).toLowerCase(), category: category}});
  }}
  return results;
}}

function transform(doc) {{ return doc; }}
""".strip()


def _ev(step: str, status: str, message: str, **extra) -> dict:
    return {"step": step, "status": status, "message": message,
            "ts": int(time.time()), **extra}
