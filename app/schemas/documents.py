from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class DocumentResponse(BaseModel):
    id: str
    project_id: str
    index_id: str | None = None
    filename: str
    size_bytes: int
    chunk_count: int
    embedding_provider: Literal["ollama", "openai"] = "ollama"
    embedding_model: str
    embedding_dimensions: int
    created_at: str
    updated_at: str
    embedding_size_bytes: int = 0
    status: Literal["completed"] = "completed"


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    filename: str = Field(min_length=1, max_length=255)
    index_id: UUID | None = None


class ChunkResponse(BaseModel):
    chunk_index: int
    content: str
    embedding_size_bytes: int
    embedding_dimensions: int


class ChunkPage(BaseModel):
    document: DocumentResponse
    items: list[ChunkResponse]
    total: int
    offset: int
    limit: int
