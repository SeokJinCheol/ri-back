from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class IndexCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default='', max_length=500)
    service_id: UUID
    service_name: str = Field(min_length=1, max_length=80)


class IndexResponse(IndexCreate):
    id: str
    project_id: str
    created_at: str
    updated_at: str
    document_count: int = 0
