from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="RAG_",
        extra="ignore",
    )

    app_name: str = "Real Iron RAG API"
    environment: Literal["local", "test", "production"] = "local"
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:5174", "http://127.0.0.1:5174", "null"]
    document_db_path: Path = PROJECT_ROOT / "data" / "documents.sqlite3"
    max_embedding_bytes: int = Field(default=100_000_000, gt=0, le=100_000_000)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    chunk_size: int = Field(default=800, ge=100, le=8000)
    chunk_overlap: int = Field(default=100, ge=0)
    max_document_chars: int = Field(default=1_000_000, gt=0)
    embedding_provider: Literal["ollama", "openai"] = "ollama"
    openai_api_key: SecretStr = Field(default=SecretStr(""), validation_alias=AliasChoices("RAG_OPENAI_API_KEY", "OPENAI_API_KEY"))
    openai_embedding_model: Literal["text-embedding-3-small", "text-embedding-3-large"] = "text-embedding-3-small"
    embedding_base_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "embeddinggemma"
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    embedding_timeout_seconds: float = Field(default=120, gt=0)

    @model_validator(mode="after")
    def validate_chunking(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
