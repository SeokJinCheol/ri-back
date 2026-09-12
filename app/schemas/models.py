from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ModelWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    provider: Literal["openai", "ollama"]
    model: str = Field(min_length=1, max_length=100)
    api_key: SecretStr | None = None

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        key = value.get_secret_value().strip()
        if not key or len(key) > 4096 or any(c.isspace() for c in key):
            raise ValueError("API 키 형식을 확인하세요.")
        return SecretStr(key)

    @model_validator(mode="after")
    def validate_model(self) -> "ModelWrite":
        if self.provider == "openai" and self.model not in {"text-embedding-3-small", "text-embedding-3-large"}:
            raise ValueError("지원하지 않는 OpenAI 임베딩 모델입니다.")
        if self.provider == "ollama" and self.api_key is not None:
            raise ValueError("Ollama는 API 키를 사용하지 않습니다.")
        return self


class ModelResponse(BaseModel):
    id: str
    project_id: str
    name: str
    provider: Literal["openai", "ollama"]
    model: str
    has_api_key: bool
    created_at: str
