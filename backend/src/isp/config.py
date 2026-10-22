# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Application configuration loaded from environment variables and .env file.
All settings have safe defaults for local Docker-based development.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central settings object.  Populated by pydantic-settings from environment
    variables (case-insensitive) or the .env file in the working directory.
    """
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    typesense_host: str = "typesense"
    typesense_port: int = 8108
    typesense_api_key: str = "local-dev-key-change-me"
    database_url: str = "postgresql://isp:isp-dev-password@isp-db:5432/isp"
    voyage_api_key: str = ""
    openai_api_key: str = ""          # OpenAI API key; stored in app_settings at runtime
    redis_url: str = "redis://isp-redis:6379/0"
    embed_cache_ttl: int = 86400   # seconds Redis TTL (24h)
    embed_lru_size: int = 2048     # L1 in-memory slots per pod
    extensions_dir: str = "/app/extensions"   # directory scanned for *.js extension files
    embed_model: str = "voyage-3"             # default Voyage AI model; overridden by app_settings DB row at runtime
    host: str = "0.0.0.0"
    port: int = 8001
    log_level: str = "INFO"   # DEBUG | INFO | WARNING | ERROR | CRITICAL

    # ── Auth (Approach 3 — OAuth2 / OIDC SSO) ─────────────────────────────────
    # All disabled by default; existing behaviour is unchanged when auth_enabled=false.
    auth_enabled: bool = False
    oauth_provider: str = "google"           # "google" | "github" | "microsoft"
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_allowed_domain: str = ""           # e.g. "yourcompany.com" — empty = any account
    oauth_tenant_id: str = "common"          # Microsoft only: "common" | "organizations" | <tenant-id>
    oauth_redirect_uri: str = "http://localhost:8001/auth/callback"
    oauth_frontend_url: str = "http://localhost:3001"  # where the SPA lives; token passed as ?isp_token=
    jwt_secret: str = "change-me-in-production"
    jwt_ttl_minutes: int = 480               # 8 hours


settings = Settings()
