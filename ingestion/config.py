from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[1]


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
    # gemini | local — never mix models in the same vector column
    embedding_provider: str = "gemini"
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Dims for the active Gemini hosted model (local BGE is always 384)
    embedding_dims: int = 768
    embed_batch_size: int = 8

    @property
    def chat_api_key(self) -> str:
        return (self.gemini_chat_api_key or self.gemini_api_key or "").strip()

    @property
    def embedding_api_key(self) -> str:
        return (self.gemini_embedding_api_key or self.gemini_api_key or "").strip()

    site_base_url: str = "https://www.srmap.edu.in"
    crawl_user_agent: str = "SRMAP-RAG-Bot/0.1 (+research; contact=local-dev)"
    crawl_rps: float = 1.5
    crawl_concurrency: int = 4

    data_dir: Path = ROOT / "data"
    manifest_path: Path = ROOT / "data" / "manifest.jsonl"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"


def get_settings() -> Settings:
    return Settings()
