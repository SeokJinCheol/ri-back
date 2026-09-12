from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.config import get_settings
from app.schemas.indices import IndexCreate, IndexResponse
from app.services.documents import DocumentError
from app.services.indices import IndexService

router = APIRouter(prefix='/projects/{project_id}/indices', tags=['indices'])


def get_index_service() -> IndexService:
    return IndexService(get_settings())


Service = Annotated[IndexService, Depends(get_index_service)]


@router.get('', response_model=list[IndexResponse])
def list_indices(project_id: UUID, service: Service):
    return service.list_indices(str(project_id))


@router.post('', response_model=IndexResponse, status_code=201)
def create_index(project_id: UUID, payload: IndexCreate, service: Service):
    try:
        return service.create(str(project_id), payload)
    except DocumentError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get('/{index_id}', response_model=IndexResponse)
def get_index(project_id: UUID, index_id: UUID, service: Service):
    try:
        return service.get_index(str(project_id), str(index_id))
    except DocumentError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.delete('/{index_id}', status_code=204)
def delete_index(project_id: UUID, index_id: UUID, service: Service):
    try:
        service.delete(str(project_id), str(index_id))
        return Response(status_code=204)
    except DocumentError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.put('/{index_id}', response_model=IndexResponse)
def update_index(project_id: UUID, index_id: UUID, payload: IndexCreate, service: Service):
    try:
        return service.update(str(project_id), str(index_id), payload)
    except DocumentError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
