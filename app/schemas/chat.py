from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    question: str = Field(min_length=1, max_length=4000)
    index_ids: list[UUID] = Field(min_length=1, max_length=100)
    top_k: int | None = Field(default=None, ge=1, le=20, strict=True)


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    index_ids: list[UUID] = Field(min_length=1, max_length=100)


class MessageCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    question: str = Field(min_length=1, max_length=4000)
    client_request_id: UUID


class Citation(BaseModel):
    number: int
    document_id: str
    chunk_index: int
    filename: str
    content: str
    score: float
    available: bool = True


class Answer(BaseModel):
    id: str
    question: str
    answer: str
    status: Literal['completed', 'no_sources', 'insufficient_evidence', 'unavailable']
    citations: list[Citation]
    created_at: str
