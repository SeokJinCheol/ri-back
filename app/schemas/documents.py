from typing import Literal
from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: str
    project_id: str
    filename: str
    size_bytes: int
    chunk_count: int
    embedding_provider: Literal["ollama", "openai"] = "ollama"
    embedding_model: str
    embedding_dimensions: int
    created_at: str
    status: Literal["completed"] = "completed"
