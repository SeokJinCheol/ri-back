from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.core.config import get_settings
from app.schemas.documents import DocumentResponse
from app.services.documents import DocumentError, DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


def get_document_service() -> DocumentService:
    return DocumentService(get_settings())


@router.get("", response_model=list[DocumentResponse])
def list_documents(project_id: UUID, service: Annotated[DocumentService, Depends(get_document_service)]):
    return service.list_documents(str(project_id))


@router.post("", response_model=DocumentResponse, status_code=201)
def upload_document(
    project_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    service: Annotated[DocumentService, Depends(get_document_service)],
    embedding_provider: Annotated[Literal["ollama", "openai"] | None, Form()] = None,
    embedding_model: Annotated[str | None, Form(max_length=100)] = None,
):
    try:
        if embedding_provider is not None or embedding_model is not None:
            service = service.for_embedding(embedding_provider, embedding_model)
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 255:
            raise DocumentError(422, "유효한 파일 이름이 필요합니다 (최대 255자).")
        data = file.file.read(service.settings.max_upload_bytes + 1)
        return service.ingest(str(project_id), filename, data)
    except DocumentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    finally:
        file.file.close()
