# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
/v1/settings — Application settings management.

Exposes one resource: the active embedding model, which can be one of the
built-in Voyage AI models, the OpenAI ``text-embedding-3-small`` model, or a
user-supplied custom OpenAI-compatible endpoint.

The setting is persisted in the ``app_settings`` PostgreSQL table so it
survives container restarts, and applied immediately at runtime so no
restart is required after a change.

Switching to a model with a **different vector dimension** (e.g. from
``voyage-3`` at 1024-d to ``text-embedding-3-small`` at 1536-d) will break
semantic search on any index whose Typesense collection was created with the
old dimension.  Those indexes must be fully rebuilt before search works again.
The PUT response includes ``dim_changed: true`` when this situation arises.
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from isp.services.domain.settings_service import (
    AVAILABLE_MODELS,
    AVAILABLE_CHAT_MODELS,
    DIM_BY_MODEL,
    PROVIDER_BY_MODEL,
    get_embed_model,
    get_openai_api_key,
    get_rexbert_config,
    get_custom_config,
    set_embed_config,
    set_setting,
    get_customer_context_enabled,
    set_customer_context_enabled,
    get_cc_vector_blending_enabled,
    get_cc_affinity_boost_enabled,
    get_cc_viewed_penalty_enabled,
    set_customer_context_flags,
    get_chat_config,
    set_chat_config,
    get_smart_followup_enabled,
    get_setting,
)
from isp.services.infra import embedding_service
from isp.services.infra.chat import service as chat_service

router = APIRouter(prefix="/settings", tags=["settings"])


class EmbedModelUpdate(BaseModel):
    """
    Request body for updating the active embedding model.

    Fields beyond ``model_id`` are only relevant for specific providers:
    - ``openai_api_key``    — required the first time an OpenAI model is selected
                              (or to update a stored key); retained across saves otherwise.
    - ``custom_endpoint``   — required when ``model_id == "custom"``
    - ``custom_api_key``    — optional; sent as ``Authorization: Bearer`` if provided
    - ``custom_model_name`` — required when ``model_id == "custom"``
    - ``custom_num_dim``    — required when ``model_id == "custom"``; vector dimension
    """

    model_id: str
    openai_api_key: str | None = None
    rexbert_endpoint: str | None = None
    rexbert_api_key: str | None = None
    custom_endpoint: str | None = None
    custom_api_key: str | None = None
    custom_model_name: str | None = None
    custom_num_dim: int | None = None


class SettingsResponse(BaseModel):
    """Full settings payload returned by GET and PUT /v1/settings."""

    embed_model: str
    """Id of the currently active embedding model."""

    embed_model_num_dim: int
    """Vector dimension produced by the current model."""

    available_models: list[dict]
    """Catalogue of all supported models with id, label, description, num_dim, and provider."""

    openai_api_key_set: bool = False
    """True when an OpenAI API key has been stored (key itself is never returned)."""

    rexbert_endpoint: str | None = None
    """Stored RexBERT endpoint URL (returned for display; shared by all rexbert-* variants)."""

    rexbert_api_key_set: bool = False
    """True when a RexBERT API key has been stored (key itself is never returned)."""

    custom_endpoint: str | None = None
    """Stored custom endpoint URL (returned for display in the UI)."""

    custom_model_name: str | None = None
    """Stored custom model name."""

    custom_num_dim: int | None = None
    """Stored custom vector dimension."""

    customer_context_enabled: bool = True
    """Master switch — when False, all action-history personalisation is skipped."""

    cc_vector_blending_enabled: bool = True
    """When True, viewed-item vectors are blended into the query embedding for ANN retrieval."""

    cc_affinity_boost_enabled: bool = True
    """When True, category / manufacturer / type affinity boosts are applied after RRF."""

    cc_viewed_penalty_enabled: bool = True
    """When True, already-viewed items receive a -0.05 score demotion to surface variety."""



class UpdateSettingsResponse(SettingsResponse):
    """Extended response returned by PUT /v1/settings, including a dimension-change flag."""

    dim_changed: bool
    """
    True when the new model's vector dimension differs from the previous model's
    dimension.  When true, all existing ISP-managed indexes must be fully rebuilt
    before semantic search will work correctly.
    """


async def _build_response(model_id: str) -> dict:
    """
    Build the common fields shared by ``SettingsResponse`` and ``UpdateSettingsResponse``.

    Fetches the stored OpenAI key presence flag and custom config from the DB
    so the UI can pre-populate the settings form on the next load.

    :param model_id: Currently active model id.
    :returns:        Dict ready to be unpacked into a response model constructor.
    """
    provider = PROVIDER_BY_MODEL.get(model_id, "voyage")

    # Determine effective dimension
    if model_id == "custom":
        cfg = await get_custom_config()
        num_dim = cfg["num_dim"]
    else:
        num_dim = DIM_BY_MODEL.get(model_id, 1024)

    openai_key  = await get_openai_api_key()
    rexbert_cfg = await get_rexbert_config()
    custom_cfg  = await get_custom_config()

    return {
        "embed_model": model_id,
        "embed_model_num_dim": num_dim,
        "available_models": AVAILABLE_MODELS,
        "openai_api_key_set":  bool(openai_key),
        "rexbert_endpoint":    rexbert_cfg["endpoint"] or None,
        "rexbert_api_key_set": bool(rexbert_cfg["api_key"]),
        "custom_endpoint":   custom_cfg["endpoint"]   or None,
        "custom_model_name": custom_cfg["model_name"] or None,
        "custom_num_dim":    custom_cfg["num_dim"]    or None,
        "customer_context_enabled":   get_customer_context_enabled(),
        "cc_vector_blending_enabled": get_cc_vector_blending_enabled(),
        "cc_affinity_boost_enabled":  get_cc_affinity_boost_enabled(),
        "cc_viewed_penalty_enabled":  get_cc_viewed_penalty_enabled(),
    }


class CustomerContextUpdate(BaseModel):
    """
    Request body for updating customer-context personalisation flags.

    All fields are optional — only the supplied fields are changed, allowing
    each toggle in the UI to send a minimal partial update.

    - ``enabled``                 — master switch; False skips all CC logic
    - ``vector_blending_enabled`` — blend viewed-item vectors into query embedding
    - ``affinity_boost_enabled``  — category / manufacturer / type post-RRF boosts
    - ``viewed_penalty_enabled``  — -0.05 demotion for already-viewed items
    """

    enabled:                 bool | None = None
    vector_blending_enabled: bool | None = None
    affinity_boost_enabled:  bool | None = None
    viewed_penalty_enabled:  bool | None = None


@router.put("/customer-context", response_model=SettingsResponse)
async def set_customer_context(body: CustomerContextUpdate) -> SettingsResponse:
    """
    Update one or more customer-context personalisation flags.

    Each field is optional — send only the flag(s) you want to change.
    Settings persist across restarts and are applied immediately at runtime.
    Useful for A/B testing individual personalisation components independently.
    """
    await set_customer_context_flags(
        enabled=body.enabled,
        vector_blending_enabled=body.vector_blending_enabled,
        affinity_boost_enabled=body.affinity_boost_enabled,
        viewed_penalty_enabled=body.viewed_penalty_enabled,
    )
    model = await get_embed_model()
    return SettingsResponse(**(await _build_response(model)))


class RexbertConfigUpdate(BaseModel):
    """Request body for saving RexBERT credentials without changing the global model."""

    endpoint: str
    api_key: str | None = None


@router.put("/rexbert-config", response_model=SettingsResponse)
async def save_rexbert_config(body: RexbertConfigUpdate) -> SettingsResponse:
    """
    Persist the RexBERT endpoint and optional API key without changing the global model.

    Useful when the global model is voyage/openai but one or more indexes use a
    per-index RexBERT override — the credentials must be stored so the embedding
    service can reach the RexBERT server at ingest and search time.
    """
    if body.endpoint.strip():
        await set_setting("rexbert_endpoint", body.endpoint.strip())
    if body.api_key is not None:
        await set_setting("rexbert_api_key", body.api_key)

    rexbert_cfg = await get_rexbert_config()
    embedding_service.set_rexbert_config(
        endpoint=rexbert_cfg["endpoint"],
        api_key=rexbert_cfg.get("api_key", ""),
    )

    model = await get_embed_model()
    return SettingsResponse(**(await _build_response(model)))


@router.get("", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    """
    Return the current application settings.

    Includes the active embedding model, its vector dimension, the full list of
    available models, and stored custom/OpenAI config so the UI can pre-populate
    its settings form.  API keys are never returned — only a boolean presence flag.
    """
    model = await get_embed_model()
    return SettingsResponse(**(await _build_response(model)))


# ── Chat configuration endpoints ─────────────────────────────────────────────

class ChatConfigUpdate(BaseModel):
    """
    Request body for updating the chat provider.

    - ``provider``       — one of: "none", "anthropic", "google", "openai", "groq", "self_hosted"
    - ``model``          — model id; empty string for self_hosted
    - ``api_key``        — cloud API key; omit to keep the previously stored key
    - ``endpoint``       — required only when provider is "self_hosted"
    - ``smart_followup``      — when True, route every follow-up query through the LLM classifier
    - ``multi_intent_split``  — when True, a message naming multiple product categories
                                ("cases and headphones") is split into a search per
                                category instead of one blended search — see
                                docs/multiIntentConjunctionSearchDesign.md
    - ``llm_response``        — when False, chat returns matched products only, with no
                                LLM call of any kind (no follow-up rewrite, no answer
                                text) and no provider needs to be configured at all.
                                True by default — opt-out, unlike the two toggles above.
    """
    provider:           str
    model:              str        = ""
    api_key:            str | None = None
    endpoint:           str | None = None
    smart_followup:     bool       = False
    multi_intent_split: bool       = False
    llm_response:       bool       = True
    local_query_router: bool       = False


class ChatConfigResponse(BaseModel):
    provider:           str
    model:              str
    endpoint:           str | None
    api_key_set:        bool
    smart_followup:     bool
    multi_intent_split: bool
    llm_response:       bool
    local_query_router: bool
    available_models:   list[dict]


@router.get("/chat", response_model=ChatConfigResponse)
async def get_chat_settings() -> ChatConfigResponse:
    """Return the current chat provider configuration."""
    cfg = await get_chat_config()
    return ChatConfigResponse(
        provider=cfg["provider"],
        model=cfg["model"],
        endpoint=cfg["endpoint"] or None,
        api_key_set=cfg["api_key_set"],
        smart_followup=cfg.get("smart_followup", False),
        multi_intent_split=cfg.get("multi_intent_split", False),
        llm_response=cfg.get("llm_response", True),
        local_query_router=cfg.get("local_query_router", False),
        available_models=AVAILABLE_CHAT_MODELS,
    )


@router.put("/chat", response_model=ChatConfigResponse)
async def update_chat_settings(body: ChatConfigUpdate) -> ChatConfigResponse:
    """
    Save the chat provider configuration and apply it immediately.

    No container restart required.  Pass ``provider="none"`` to disable chat.
    API key is not returned in responses — only a boolean presence flag.
    """
    if body.provider not in ("none", "anthropic", "google", "openai", "groq", "self_hosted"):
        from fastapi import HTTPException, status as http_status
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider '{body.provider}'.",
        )
    if body.provider == "self_hosted" and not body.endpoint:
        from fastapi import HTTPException, status as http_status
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="endpoint is required when provider is 'self_hosted'.",
        )

    # Validate the chosen model against the provider's live catalog before
    # persisting — catches a deprecated/retired model at save time instead
    # of at chat time. Uses the newly-submitted key if one was given, else
    # the already-stored key (the UI leaves the field blank to keep it).
    effective_key = body.api_key or await get_setting("chat_api_key") or ""
    try:
        await chat_service.validate_chat_model(
            body.provider, body.model, effective_key, body.endpoint or "",
        )
    except ValueError as exc:
        from fastapi import HTTPException, status as http_status
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc))

    await set_chat_config(
        provider=body.provider,
        model=body.model,
        api_key=body.api_key,
        endpoint=body.endpoint,
        smart_followup=body.smart_followup,
        multi_intent_split=body.multi_intent_split,
        llm_response=body.llm_response,
        local_query_router=body.local_query_router,
    )
    cfg = await get_chat_config()
    return ChatConfigResponse(
        provider=cfg["provider"],
        model=cfg["model"],
        endpoint=cfg["endpoint"] or None,
        api_key_set=cfg["api_key_set"],
        smart_followup=cfg.get("smart_followup", False),
        multi_intent_split=cfg.get("multi_intent_split", False),
        llm_response=cfg.get("llm_response", True),
        local_query_router=cfg.get("local_query_router", False),
        available_models=AVAILABLE_CHAT_MODELS,
    )


@router.put("", response_model=UpdateSettingsResponse)
async def update_settings(body: EmbedModelUpdate) -> UpdateSettingsResponse:
    """
    Update the active embedding model and any associated provider config.

    Persists the choice (and credentials) to the database, applies it immediately
    at runtime — no container restart required.  Returns ``dim_changed: true`` when
    the new model produces vectors of a different dimension than the previous model,
    signalling that affected indexes need a full rebuild.
    """
    previous_model = await get_embed_model()
    if previous_model == "custom":
        cfg = await get_custom_config()
        previous_dim = cfg["num_dim"] or 1024
    else:
        previous_dim = DIM_BY_MODEL.get(previous_model, 1024)

    try:
        await set_embed_config(
            body.model_id,
            openai_api_key=body.openai_api_key,
            rexbert_endpoint=body.rexbert_endpoint,
            rexbert_api_key=body.rexbert_api_key,
            custom_endpoint=body.custom_endpoint,
            custom_api_key=body.custom_api_key,
            custom_model_name=body.custom_model_name,
            custom_num_dim=body.custom_num_dim,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    base = await _build_response(body.model_id)
    new_dim = base["embed_model_num_dim"]

    return UpdateSettingsResponse(
        **base,
        dim_changed=new_dim != previous_dim,
    )
