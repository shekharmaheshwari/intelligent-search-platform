"""
Agent Setup router — one-shot AI-powered index configuration.

POST /v1/agent/setup   → SSE stream: analyse JSON + execute all setup steps
POST /v1/agent/preview → JSON: analyse only, return artifacts without executing
"""
from __future__ import annotations
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from isp.services.infra.agent import json_sampler, schema_extractor, claude_analyst, setup_executor
from isp.services.domain.settings_service import get_chat_api_key_for_agent

router = APIRouter(prefix="/v1/agent", tags=["Agent"])


class AgentSetupRequest(BaseModel):
    index_name: str
    json_path:  str
    root_key:   str = ""
    api_key:    str = ""   # Anthropic API key — falls back to chat API key if empty
    feedback:   str = ""   # Optional correction feedback from a previous attempt


class AgentPreviewRequest(BaseModel):
    index_name: str
    json_path:  str
    root_key:   str = ""
    api_key:    str = ""
    feedback:   str = ""   # Optional correction feedback from a previous attempt


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


async def _resolve_api_key(provided: str) -> str:
    if provided.strip():
        return provided.strip()
    key = await get_chat_api_key_for_agent()
    if not key:
        raise HTTPException(
            400,
            "No Anthropic API key found. Provide one in the Agent tab or configure "
            "Anthropic as your Chat provider in Settings → Chat Model."
        )
    return key


@router.post("/setup", summary="Analyse JSON and build index (SSE stream)")
async def agent_setup(body: AgentSetupRequest):
    """
    Analyse a JSON data file with Claude and execute the full ISP setup pipeline.

    Returns a Server-Sent Events stream. Each event is a JSON object:
    ``{ "step": "...", "status": "ok"|"running"|"error", "message": "..." }``

    The final event on success:
    ``{ "step": "done", "status": "ok", "index_id": "..." }``
    """
    api_key = await _resolve_api_key(body.api_key)

    async def stream():
        try:
            # 1. Load + sample
            yield _sse({"step": "load", "status": "running",
                         "message": "Reading JSON file..."})
            try:
                sample, detected_root, total = await json_sampler.load(
                    body.json_path, root_key=body.root_key)
            except FileNotFoundError:
                yield _sse({"step": "load", "status": "error",
                             "message": f"File not found: {body.json_path}"})
                return
            except Exception as e:
                yield _sse({"step": "load", "status": "error", "message": str(e)})
                return

            root_key = detected_root or body.root_key
            yield _sse({"step": "load", "status": "ok",
                         "message": f"{total} items found",
                         "root_key": root_key, "total": total})

            # 2. Extract schema
            yield _sse({"step": "schema", "status": "running",
                         "message": "Extracting schema..."})
            schema = schema_extractor.extract(sample)
            yield _sse({"step": "schema", "status": "ok",
                         "message": f"{len(schema)} fields detected",
                         "field_count": len(schema)})

            # 3. Claude analysis
            yield _sse({"step": "analyse", "status": "running",
                         "message": "Claude is reading your data structure..."})
            try:
                artifacts = await claude_analyst.analyse_with_correction(
                    sample, schema, body.index_name, api_key, feedback=body.feedback)
            except Exception as e:
                msg = str(e)
                if "credit balance" in msg or "too low" in msg:
                    msg = "Anthropic API credit balance is too low. Please top up at console.anthropic.com → Billing."
                elif "invalid_api_key" in msg or "authentication" in msg.lower():
                    msg = "Invalid Anthropic API key. Check Settings → Chat Model or the key you entered."
                yield _sse({"step": "analyse", "status": "error", "message": msg})
                return

            corrections = artifacts.get("_auto_corrections", [])
            analyse_msg = (
                f"Analysis complete — "
                f"{len(artifacts.get('field_config', []))} fields mapped, "
                f"3 extensions generated"
            )
            if corrections:
                analyse_msg += f" (auto-corrected {len(corrections)}x)"
            yield _sse({"step": "analyse", "status": "ok",
                         "message": analyse_msg,
                         "intent_label": artifacts.get("intent_label", "")})

            # 4. Execute pipeline (streams its own events)
            async for event in setup_executor.run(
                artifacts=artifacts,
                index_name=body.index_name,
                json_path=body.json_path,
                root_key=root_key,
                total_items=total,
            ):
                yield _sse(event)
                if event.get("status") == "error":
                    return

        except Exception as e:
            yield _sse({"step": "fatal", "status": "error", "message": str(e)})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no",
                                      "Cache-Control": "no-cache"})


@router.post("/preview", summary="Analyse JSON and return artifacts (no index created)")
async def agent_preview(body: AgentPreviewRequest):
    """
    Dry-run: sample the JSON file, extract schema, call Claude, and return
    the generated artifacts (JS extensions + field_config) without creating
    anything in ISP. Use this to review before committing.
    """
    api_key = await _resolve_api_key(body.api_key)

    try:
        sample, detected_root, total = await json_sampler.load(
            body.json_path, root_key=body.root_key)
    except FileNotFoundError:
        raise HTTPException(400, f"File not found inside the API container: {body.json_path}. Place the file in the project data/ folder and use path /app/data/<filename>.json")
    except Exception as e:
        raise HTTPException(400, str(e))

    schema = schema_extractor.extract(sample)

    try:
        artifacts = await claude_analyst.analyse_with_correction(
            sample, schema, body.index_name, api_key, feedback=body.feedback)
    except ValueError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        msg = str(e)
        if "credit balance" in msg or "too low" in msg:
            raise HTTPException(402, "Anthropic API credit balance is too low. Please top up at console.anthropic.com → Billing.")
        if "invalid_api_key" in msg or "authentication" in msg.lower():
            raise HTTPException(401, "Invalid Anthropic API key. Check Settings → Chat Model or the key you entered.")
        raise HTTPException(502, f"Claude API error: {msg}")

    # Apply normalizer to first sample item so the user can confirm the output
    preview_doc: dict = sample[0] if sample else {}
    try:
        import asyncio
        from isp.services.infra.extension_runner import _run_sync
        normalizer_ext = [{
            "id": "preview", "name": "preview-normalizer",
            "code": artifacts.get("normalizer_js", "function transform(doc){return doc;}"),
            "type": "transform", "enabled": True,
        }]
        result = await asyncio.to_thread(_run_sync, [dict(sample[0])], normalizer_ext)
        if result:
            from isp.services.infra.agent.setup_executor import _flatten_objects
            preview_doc = _flatten_objects(result[0])
    except Exception:
        pass  # fall back to raw sample on any JS error

    return {
        "root_key":    detected_root or body.root_key,
        "total_items": total,
        "schema":      schema,
        "artifacts":   artifacts,
        "preview_doc": preview_doc,
    }
