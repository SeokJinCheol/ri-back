from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.core.config import get_settings
from app.schemas.models import ModelResponse, ModelWrite
from app.services.documents import DocumentError
from app.services.models import ModelService


class ModelRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                response = await handler(request)
                response.headers['Cache-Control'] = 'no-store'
                return response
            except RequestValidationError as exc:
                # Validation errors may include the submitted secret or whole request body.
                raise HTTPException(422, "모델 이름, 공급자, 모델 ID와 API 키 입력을 확인하세요.") from exc
            except DocumentError as exc:
                raise HTTPException(exc.status_code, exc.detail) from exc
        return safe_handler


router = APIRouter(prefix='/projects/{project_id}/models', tags=['models'], route_class=ModelRoute)


def get_model_service() -> ModelService:
    return ModelService(get_settings())


Service = Annotated[ModelService, Depends(get_model_service)]


@router.get('', response_model=list[ModelResponse])
def list_models(project_id: UUID, service: Service):
    return service.list_models(str(project_id))


@router.post('', response_model=ModelResponse, status_code=201)
def create_model(project_id: UUID, payload: ModelWrite, service: Service):
    return service.save(str(project_id), payload)


@router.put('/{model_id}', response_model=ModelResponse)
def update_model(project_id: UUID, model_id: UUID, payload: ModelWrite, service: Service):
    return service.save(str(project_id), payload, str(model_id))


@router.delete('/{model_id}', status_code=204)
def delete_model(project_id: UUID, model_id: UUID, service: Service):
    service.delete(str(project_id), str(model_id))
    return Response(status_code=204)
