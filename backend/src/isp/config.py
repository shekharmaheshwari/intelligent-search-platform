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


settings = Settings()
