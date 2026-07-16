"""
Persistent key/value settings stored in the ``app_settings`` PostgreSQL table.

This module is the single place responsible for reading and writing application
settings that need to survive restarts.  Four embedding providers are supported:

- ``voyage``   — Voyage AI models (default; key from ``VOYAGE_API_KEY`` env var)
- ``openai``   — OpenAI embeddings API; key stored in ``app_settings`` table
- ``rexbert``  — RexBERT model family; shared endpoint + optional key stored in
                 ``app_settings``; model variant sent as the ``model`` field
- ``custom``   — Any OpenAI-compatible endpoint; endpoint, optional key, model
                 name and vector dimension are all stored in ``app_settings``

On startup ``load_embed_model()`` is called from the FastAPI lifespan to
synchronise the in-memory state in ``embedding_service`` and
``typesense_client`` with whatever the operator last saved.
"""
from __future__ import annotations
from isp.services.infra.database import get_pool
from isp.config import settings

# ── Catalogue of available embedding models ──────────────────────────────────
#
# ``num_dim`` is the vector dimension produced by each model.  ISP bakes this
# dimension into the Typesense collection schema at creation time.  Switching to
# a model with a *different* dimension requires a full rebuild of every index.
#
# ``provider`` controls which HTTP client is used:
#   "voyage"   → voyageai.AsyncClient
#   "openai"   → httpx POST to api.openai.com (key stored in app_settings)
#   "rexbert"  → httpx POST to configurable RexBERT endpoint (stored in app_settings)
#   "custom"   → httpx POST to user-supplied endpoint (config in app_settings)

AVAILABLE_MODELS: list[dict] = [
    {
        "id": "voyage-3",
        "label": "voyage-3",
        "description": "General-purpose model, best balance of quality and speed.",
        "num_dim": 1024,
        "provider": "voyage",
    },
    {
        "id": "voyage-3-large",
        "label": "voyage-3-large",
        "description": "Highest-quality embeddings for maximum precision search.",
        "num_dim": 1024,
        "provider": "voyage",
    },
    {
        "id": "voyage-3-lite",
        "label": "voyage-3-lite",
        "description": "Lightweight and fast, lower cost per token.",
        "num_dim": 512,
        "provider": "voyage",
    },
    {
        "id": "voyage-code-3",
        "label": "voyage-code-3",
        "description": "Optimised for code and technical documentation.",
        "num_dim": 1024,
        "provider": "voyage",
    },
    {
        "id": "voyage-finance-2",
        "label": "voyage-finance-2",
        "description": "Optimised for financial documents and reports.",
        "num_dim": 1024,
        "provider": "voyage",
    },
    {
        "id": "text-embedding-3-small",
        "label": "text-embedding-3-small",
        "description": "OpenAI general-purpose embedding model. Requires an OpenAI API key.",
        "num_dim": 1536,
        "provider": "openai",
    },
    {
        "id": "rexbert-micro",
        "label": "RexBERT-micro",
        "description": "Smallest RexBERT variant — ultra-low latency, 128-d vectors.",
        "num_dim": 128,
        "provider": "rexbert",
    },
    {
        "id": "rexbert-mini",
        "label": "RexBERT-mini",
        "description": "Compact RexBERT variant — fast inference, 256-d vectors.",
        "num_dim": 256,
        "provider": "rexbert",
    },
    {
        "id": "rexbert-base",
        "label": "RexBERT-base",
        "description": "Standard RexBERT variant — balanced quality and speed, 768-d vectors.",
        "num_dim": 768,
        "provider": "rexbert",
    },
    {
        "id": "rexbert-large",
        "label": "RexBERT-large",
        "description": "Largest RexBERT variant — highest quality, 1024-d vectors.",
        "num_dim": 1024,
        "provider": "rexbert",
    },
    {
        "id": "custom",
        "label": "Custom",
        "description": "User-defined OpenAI-compatible embedding endpoint.",
        "num_dim": 0,  # dimension is user-supplied
        "provider": "custom",
    },
]

# Static dimension lookup; custom model's dim comes from app_settings at runtime
DIM_BY_MODEL: dict[str, int] = {
    m["id"]: m["num_dim"] for m in AVAILABLE_MODELS if m["id"] != "custom"
}

PROVIDER_BY_MODEL: dict[str, str] = {m["id"]: m["provider"] for m in AVAILABLE_MODELS}


# ── Customer context (in-process cached flags) ───────────────────────────────
#
# Each flag is stored in ``app_settings`` and cached in a module-level variable
# so search routers can check them on every request without a DB round-trip.
# All default to ``True`` so existing deployments continue to personalise as
# before until an operator explicitly disables a feature.
#
# Flags:
#   customer_context_enabled      — master toggle; disabling skips all CC logic
#   cc_vector_blending_enabled    — blend viewed-item vectors into the query vec
#   cc_affinity_boost_enabled     — apply category/manufacturer/type score boosts
#   cc_viewed_penalty_enabled     — apply -0.05 demotion for already-viewed items
#
_customer_context_enabled:   bool = True
_cc_vector_blending_enabled: bool = True
_cc_affinity_boost_enabled:  bool = True
_cc_viewed_penalty_enabled:  bool = True


def get_customer_context_enabled() -> bool:
    return _customer_context_enabled

def get_cc_vector_blending_enabled() -> bool:
    return _cc_vector_blending_enabled

def get_cc_affinity_boost_enabled() -> bool:
    return _cc_affinity_boost_enabled

def get_cc_viewed_penalty_enabled() -> bool:
    return _cc_viewed_penalty_enabled


async def set_customer_context_flags(
    *,
    enabled: bool | None = None,
    vector_blending_enabled: bool | None = None,
    affinity_boost_enabled: bool | None = None,
    viewed_penalty_enabled: bool | None = None,
) -> None:
    """
    Persist one or more customer-context flags and update the in-process cache.

    Only the fields that are not ``None`` are written — partial updates are safe.
    Per-dimension affinity configuration is now stored per-index in the
    ``affinity_dimensions`` column, not as global flags here.
    """
    global _customer_context_enabled, _cc_vector_blending_enabled
    global _cc_affinity_boost_enabled, _cc_viewed_penalty_enabled

    async def _set_bool(key: str, val: bool) -> None:
        await set_setting(key, "true" if val else "false")

    if enabled is not None:
        _customer_context_enabled = enabled
        await _set_bool("customer_context_enabled", enabled)

    if vector_blending_enabled is not None:
        _cc_vector_blending_enabled = vector_blending_enabled
        await _set_bool("cc_vector_blending_enabled", vector_blending_enabled)

    if affinity_boost_enabled is not None:
        _cc_affinity_boost_enabled = affinity_boost_enabled
        await _set_bool("cc_affinity_boost_enabled", affinity_boost_enabled)

    if viewed_penalty_enabled is not None:
        _cc_viewed_penalty_enabled = viewed_penalty_enabled
        await _set_bool("cc_viewed_penalty_enabled", viewed_penalty_enabled)


async def set_customer_context_enabled(enabled: bool) -> None:
    """Thin wrapper kept for backward compatibility; delegates to set_customer_context_flags."""
    await set_customer_context_flags(enabled=enabled)


async def get_setting(key: str) -> str | None:
    """
    Read a single setting from the ``app_settings`` table.

    Returns ``None`` when the key has never been written, allowing callers to
    fall back to compile-time defaults in ``config.py``.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM app_settings WHERE key = $1", key)
        return row["value"] if row else None


async def set_setting(key: str, value: str) -> None:
    """
    Upsert a setting into the ``app_settings`` table.

    Uses ``ON CONFLICT DO UPDATE`` so the call is safe whether the key already
    exists or is being written for the first time.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            key, value,
        )


async def get_embed_model() -> str:
    """
    Return the currently active embedding model id.

    Reads from the DB first; falls back to ``settings.embed_model`` (defaults
    to ``"voyage-3"``) when no DB row exists yet.
    """
    saved = await get_setting("embed_model")
    return saved or settings.embed_model


async def get_openai_api_key() -> str:
    """
    Return the stored OpenAI API key, or the compile-time default from config.

    The key is stored in ``app_settings`` so operators can set it via the UI
    without modifying environment variables.
    """
    saved = await get_setting("openai_api_key")
    return saved or settings.openai_api_key


async def get_rexbert_config() -> dict:
    """
    Return the shared RexBERT endpoint configuration from ``app_settings``.

    All RexBERT variants share one endpoint URL and optional API key.
    The specific model variant is sent as the ``model`` field in each request.

    Fields returned:
    - ``endpoint`` — full URL of the RexBERT embeddings endpoint
    - ``api_key``  — bearer token (empty string if not required)
    """
    endpoint = await get_setting("rexbert_endpoint") or ""
    api_key  = await get_setting("rexbert_api_key")  or ""
    return {"endpoint": endpoint, "api_key": api_key}


async def get_custom_config() -> dict:
    """
    Return all custom-endpoint configuration values from ``app_settings``.

    Fields returned:
    - ``endpoint``   — full URL of the OpenAI-compatible embeddings endpoint
    - ``api_key``    — bearer token (empty string if not required by the endpoint)
    - ``model_name`` — model name sent in the request body
    - ``num_dim``    — integer vector dimension expected from the endpoint
    """
    endpoint   = await get_setting("custom_endpoint") or ""
    api_key    = await get_setting("custom_api_key") or ""
    model_name = await get_setting("custom_model_name") or ""
    raw_dim    = await get_setting("custom_num_dim") or "0"
    return {
        "endpoint":   endpoint,
        "api_key":    api_key,
        "model_name": model_name,
        "num_dim":    int(raw_dim),
    }


async def set_embed_config(
    model_id: str,
    *,
    openai_api_key: str | None = None,
    rexbert_endpoint: str | None = None,
    rexbert_api_key: str | None = None,
    custom_endpoint: str | None = None,
    custom_api_key: str | None = None,
    custom_model_name: str | None = None,
    custom_num_dim: int | None = None,
) -> None:
    """
    Persist a new embedding configuration and apply it immediately at runtime.

    Handles all four providers:

    - **voyage** models — only ``model_id`` is required; Voyage key comes from
      the ``VOYAGE_API_KEY`` environment variable.
    - **openai** models (e.g. ``text-embedding-3-small``) — ``openai_api_key``
      is persisted if provided; the previously stored key is retained otherwise.
    - **rexbert** models — ``rexbert_endpoint`` is persisted if provided; all
      four variants share the same endpoint, with the model id sent as the
      ``model`` field.  ``rexbert_api_key`` is optional.
    - **custom** provider — ``custom_endpoint``, ``custom_model_name``, and
      ``custom_num_dim`` are required; ``custom_api_key`` is optional.

    Raises ``ValueError`` for unknown model ids or missing required fields.

    After persisting, updates ``embedding_service`` and ``typesense_client``
    in-process so that subsequent requests use the new config without a restart.
    """
    if model_id not in PROVIDER_BY_MODEL:
        raise ValueError(
            f"Unknown model '{model_id}'. Choose from: {list(PROVIDER_BY_MODEL)}"
        )

    provider = PROVIDER_BY_MODEL[model_id]

    if provider == "openai" and openai_api_key:
        await set_setting("openai_api_key", openai_api_key)

    if provider == "rexbert":
        if rexbert_endpoint:
            await set_setting("rexbert_endpoint", rexbert_endpoint)
        if rexbert_api_key is not None:
            await set_setting("rexbert_api_key", rexbert_api_key)

    if provider == "custom":
        if not custom_endpoint:
            raise ValueError("custom_endpoint is required when model_id is 'custom'")
        if not custom_model_name:
            raise ValueError("custom_model_name is required when model_id is 'custom'")
        if not custom_num_dim or custom_num_dim <= 0:
            raise ValueError("custom_num_dim must be a positive integer when model_id is 'custom'")
        await set_setting("custom_endpoint",   custom_endpoint)
        await set_setting("custom_model_name", custom_model_name)
        await set_setting("custom_num_dim",    str(custom_num_dim))
        if custom_api_key is not None:
            await set_setting("custom_api_key", custom_api_key)

    await set_setting("embed_model", model_id)

    # Apply immediately — no restart required
    from isp.services.infra import embedding_service, typesense_client

    if provider == "custom":
        cfg = await get_custom_config()
        embedding_service.set_current_model(model_id)
        embedding_service.set_custom_config(
            endpoint=cfg["endpoint"],
            api_key=cfg["api_key"],
            model_name=cfg["model_name"],
            num_dim=cfg["num_dim"],
        )
        typesense_client.set_vec_dim(cfg["num_dim"])
    elif provider == "rexbert":
        cfg = await get_rexbert_config()
        embedding_service.set_current_model(model_id)
        embedding_service.set_rexbert_config(
            endpoint=cfg["endpoint"],
            api_key=cfg["api_key"],
        )
        typesense_client.set_vec_dim(DIM_BY_MODEL[model_id])
    elif provider == "openai":
        key = await get_openai_api_key()
        embedding_service.set_current_model(model_id)
        embedding_service.set_openai_api_key(key)
        typesense_client.set_vec_dim(DIM_BY_MODEL[model_id])
    else:
        embedding_service.set_current_model(model_id)
        typesense_client.set_vec_dim(DIM_BY_MODEL[model_id])


async def set_embed_model(model_id: str) -> None:
    """
    Thin wrapper kept for backward compatibility.  Delegates to ``set_embed_config``.
    """
    await set_embed_config(model_id)


# ── Chat model configuration ─────────────────────────────────────────────────
#
# Two categories of providers:
#   self_hosted — user supplies an OpenAI-compatible endpoint URL
#   paid        — cloud API; user supplies an API key; URL is hardcoded per provider
#
# Default provider is "none" — chat feature is disabled until the operator
# saves a configuration via the Settings → Chat panel.

AVAILABLE_CHAT_MODELS: list[dict] = [
    # Anthropic (paid)
    {"id": "claude-haiku-4-5",          "label": "Claude Haiku 4.5",       "provider": "anthropic",   "tier": "paid"},
    {"id": "claude-sonnet-4-6",         "label": "Claude Sonnet 4.6",      "provider": "anthropic",   "tier": "paid"},
    # Google (paid) — 1.5 models deprecated; use 2.x+
    {"id": "gemini-2.5-flash",          "label": "Gemini 2.5 Flash",       "provider": "google",      "tier": "paid"},
    {"id": "gemini-2.0-flash",          "label": "Gemini 2.0 Flash",       "provider": "google",      "tier": "paid"},
    {"id": "gemini-2.5-pro",            "label": "Gemini 2.5 Pro",         "provider": "google",      "tier": "paid"},
    # OpenAI (paid)
    {"id": "gpt-4o-mini",               "label": "GPT-4o Mini",            "provider": "openai",      "tier": "paid"},
    {"id": "gpt-4o",                    "label": "GPT-4o",                 "provider": "openai",      "tier": "paid"},
    # Groq (paid — free tier available)
    {"id": "llama-3.1-8b-instant",      "label": "Llama 3.1 8B (Groq)",   "provider": "groq",        "tier": "paid"},
    {"id": "llama-3.3-70b-versatile",   "label": "Llama 3.3 70B (Groq)",  "provider": "groq",        "tier": "paid"},
    # Self-hosted (free — user provides endpoint)
    {"id": "self_hosted",               "label": "Self-hosted (Ollama / vLLM / LocalAI)", "provider": "self_hosted", "tier": "self_hosted"},
]


async def get_chat_config() -> dict:
    """Return the stored chat provider configuration."""
    provider = await get_setting("chat_provider") or "none"
    model    = await get_setting("chat_model")    or ""
    endpoint = await get_setting("chat_endpoint") or ""
    key_set  = bool(await get_setting("chat_api_key"))
    return {"provider": provider, "model": model, "endpoint": endpoint, "api_key_set": key_set}


async def set_chat_config(
    *,
    provider: str,
    model: str,
    api_key: str | None = None,
    endpoint: str | None = None,
) -> None:
    """Persist chat provider configuration and apply it immediately at runtime."""
    await set_setting("chat_provider", provider)
    await set_setting("chat_model",    model)
    if api_key is not None:
        await set_setting("chat_api_key", api_key)
    if endpoint is not None:
        await set_setting("chat_endpoint", endpoint)

    from isp.services.infra.chat import service as chat_service
    stored_key      = await get_setting("chat_api_key")  or ""
    stored_endpoint = await get_setting("chat_endpoint") or ""
    chat_service.set_chat_config(
        provider=provider,
        model=model,
        api_key=stored_key,
        endpoint=stored_endpoint,
    )


async def load_chat_config() -> None:
    """Restore chat provider state from DB on startup."""
    provider = await get_setting("chat_provider") or "none"
    model    = await get_setting("chat_model")    or ""
    api_key  = await get_setting("chat_api_key")  or ""
    endpoint = await get_setting("chat_endpoint") or ""

    from isp.services.infra.chat import service as chat_service
    chat_service.set_chat_config(
        provider=provider,
        model=model,
        api_key=api_key,
        endpoint=endpoint,
    )


async def load_embed_model() -> None:
    """
    Synchronise runtime state with the persisted model setting.

    Called once from the FastAPI lifespan on startup so that a previously saved
    model choice — including custom endpoint config or OpenAI key — takes effect
    immediately, even across container restarts.
    """
    model_id = await get_embed_model()
    provider  = PROVIDER_BY_MODEL.get(model_id, "voyage")

    from isp.services.infra import embedding_service, typesense_client

    embedding_service.set_current_model(model_id)

    if provider == "custom":
        cfg = await get_custom_config()
        embedding_service.set_custom_config(
            endpoint=cfg["endpoint"],
            api_key=cfg["api_key"],
            model_name=cfg["model_name"],
            num_dim=cfg["num_dim"],
        )
        typesense_client.set_vec_dim(cfg["num_dim"] or 1024)
    elif provider == "rexbert":
        cfg = await get_rexbert_config()
        embedding_service.set_rexbert_config(
            endpoint=cfg["endpoint"],
            api_key=cfg["api_key"],
        )
        typesense_client.set_vec_dim(DIM_BY_MODEL.get(model_id, 768))
    elif provider == "openai":
        key = await get_openai_api_key()
        embedding_service.set_openai_api_key(key)
        typesense_client.set_vec_dim(DIM_BY_MODEL.get(model_id, 1536))
    else:
        typesense_client.set_vec_dim(DIM_BY_MODEL.get(model_id, 1024))

    # Always load RexBERT config so per-index model overrides work even when
    # the global active model is not RexBERT.  get_rexbert_config returns empty
    # strings when no endpoint has been configured — safe to call unconditionally.
    if provider != "rexbert":
        rexbert_cfg = await get_rexbert_config()
        if rexbert_cfg.get("endpoint"):
            embedding_service.set_rexbert_config(
                endpoint=rexbert_cfg["endpoint"],
                api_key=rexbert_cfg.get("api_key", ""),
            )

    # Restore customer-context flags from ``app_settings`` on startup.
    global _customer_context_enabled, _cc_vector_blending_enabled
    global _cc_affinity_boost_enabled, _cc_viewed_penalty_enabled

    _bool = lambda v: v.lower() != "false"  # "false" → False, anything else → True

    for _key, _var in {
        "customer_context_enabled":   "_customer_context_enabled",
        "cc_vector_blending_enabled": "_cc_vector_blending_enabled",
        "cc_affinity_boost_enabled":  "_cc_affinity_boost_enabled",
        "cc_viewed_penalty_enabled":  "_cc_viewed_penalty_enabled",
    }.items():
        _v = await get_setting(_key)
        if _v is not None:
            globals()[_var] = _bool(_v)
