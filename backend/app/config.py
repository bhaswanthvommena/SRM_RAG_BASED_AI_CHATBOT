from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://srmap:srmap@localhost:5432/srmap_rag"
    # Legacy single key (fallback if chat/embed keys unset)
    gemini_api_key: str = ""
    # Prefer separate keys to split quota across accounts
    gemini_chat_api_key: str = ""
    gemini_embedding_api_key: str = ""
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_chat_model: str = "gemini-3.5-flash-lite"
    embedding_provider: str = "gemini"
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dims: int = 768
    cors_origins: str = "http://localhost:3000"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def chat_api_key(self) -> str:
        return (self.gemini_chat_api_key or self.gemini_api_key or "").strip()

    @property
    def embedding_api_key(self) -> str:
        return (self.gemini_embedding_api_key or self.gemini_api_key or "").strip()


def get_settings() -> Settings:
    return Settings()
