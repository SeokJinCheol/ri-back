from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Query, Response

from app.core.config import get_settings
from app.schemas.documents import DocumentResponse, DocumentUpdate, ChunkPage
from app.services.documents import DocumentError, DocumentService

from app.services.models import ModelService

router = APIRouter(prefix="/documents", tags=["documents"])


def get_document_service() -> DocumentService:
    return DocumentService(get_settings())


@router.get("", response_model=list[DocumentResponse])
def list_documents(project_id: UUID, service: Annotated[DocumentService, Depends(get_document_service)], index_id: UUID | None = None):
    try:
        return service.list_documents(str(project_id), str(index_id) if index_id is not None else None)
    except DocumentError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.post("", response_model=DocumentResponse, status_code=201)
def upload_document(
    project_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    service: Annotated[DocumentService, Depends(get_document_service)],
    index_id: Annotated[UUID | None, Form()] = None,
    model_config_id: Annotated[UUID | None, Form()] = None,
    embedding_provider: Annotated[Literal["ollama", "openai"] | None, Form()] = None,
    embedding_model: Annotated[str | None, Form(max_length=100)] = None,
):
    try:
        if model_config_id is not None:
            if embedding_provider is not None or embedding_model is not None:
                raise DocumentError(422, "등록 모델과 공급자 직접 설정을 동시에 지정할 수 없습니다.")
            service = DocumentService(ModelService(service.settings).embedding_settings(str(project_id), str(model_config_id)))
        elif embedding_provider is not None or embedding_model is not None:
            service = service.for_embedding(embedding_provider, embedding_model)
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 255:
            raise DocumentError(422, "유효한 파일 이름이 필요합니다 (최대 255자).")
        data = file.file.read(service.settings.max_upload_bytes + 1)
        return service.ingest(str(project_id), filename, data, str(index_id) if index_id is not None else None)
    except DocumentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    finally:
        file.file.close()


@router.get('/{document_id}/chunks', response_model=ChunkPage)
def list_chunks(project_id: UUID, document_id: UUID, service: Annotated[DocumentService, Depends(get_document_service)],
                offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50):
    try:
        return service.list_chunks(str(project_id), str(document_id), offset, limit)
    except DocumentError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.put('/{document_id}', response_model=DocumentResponse)
def update_document(project_id: UUID, document_id: UUID, payload: DocumentUpdate, service: Annotated[DocumentService, Depends(get_document_service)]):
    try:
        return service.update_document(str(project_id), str(document_id), payload)
    except DocumentError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.delete('/{document_id}', status_code=204)
def delete_document(project_id: UUID, document_id: UUID, service: Annotated[DocumentService, Depends(get_document_service)]):
    try:
        service.delete_document(str(project_id), str(document_id))
        return Response(status_code=204)
    except DocumentError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
