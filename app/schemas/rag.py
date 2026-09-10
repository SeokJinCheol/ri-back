from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)


class SourceDocument(BaseModel):
    document_id: str
    content: str
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceDocument] = Field(default_factory=list)
