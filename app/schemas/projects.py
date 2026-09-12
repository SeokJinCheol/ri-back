from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectMember(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    role: Literal['admin', 'member'] = 'member'
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=200)
    members: list[ProjectMember] = Field(default_factory=list, max_length=100)

    @field_validator("members")
    @classmethod
    def unique_members(cls, members: list[ProjectMember]) -> list[ProjectMember]:
        if len({member.email for member in members}) != len(members):
            raise ValueError("동일한 이메일을 중복 등록할 수 없습니다.")
        return members


class ProjectUpdate(ProjectCreate):
    pass


class ProjectResponse(ProjectCreate):
    id: str
    created_at: str
    updated_at: str
    creator_email: str | None = None
