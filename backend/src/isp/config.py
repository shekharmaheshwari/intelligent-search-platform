from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    typesense_host: str = "typesense"
    typesense_port: int = 8108
    typesense_api_key: str = "local-dev-key-change-me"
    database_url: str = "postgresql://isp:isp-dev-password@isp-db:5432/isp"
    voyage_api_key: str = ""
    host: str = "0.0.0.0"
    port: int = 8001


settings = Settings()
