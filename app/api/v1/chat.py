from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from app.services.documents import DocumentError

from app.core.config import get_settings
from app.schemas.chat import Answer, ConversationCreate, MessageCreate, SearchRequest
from app.services.chat import ChatService

class ChatRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                response = await handler(request)
                response.headers['Cache-Control'] = 'no-store'
                return response
            except RequestValidationError as exc:
                raise HTTPException(422, '질문(1~4,000자), 검색 인덱스, 요청 ID와 사용자 정보를 확인하세요.') from exc
            except DocumentError as exc:
                raise HTTPException(exc.status_code, exc.detail) from exc
        return safe_handler


router = APIRouter(tags=['chat'], route_class=ChatRoute)


def get_chat_service():
    return ChatService(get_settings())


Service = Annotated[ChatService, Depends(get_chat_service)]
Actor = Annotated[str, Header(alias='X-User-Email', min_length=3, max_length=254)]


@router.post('/projects/{project_id}/services/{service_id}/search')
def search(project_id: UUID, service_id: UUID, payload: SearchRequest, service: Service, actor: Actor):
    return {'sources': service.search(str(project_id), str(service_id), actor, payload)}


@router.get('/projects/{project_id}/services/{service_id}/conversations')
def conversations(project_id: UUID, service_id: UUID, service: Service, actor: Actor):
    return service.list(str(project_id), str(service_id), actor)


@router.post('/projects/{project_id}/services/{service_id}/conversations', status_code=201)
def create(project_id: UUID, service_id: UUID, payload: ConversationCreate, service: Service, actor: Actor):
    return service.create(str(project_id), str(service_id), actor, payload)


@router.get('/conversations/{conversation_id}/messages', response_model=list[Answer])
def messages(conversation_id: UUID, service: Service, actor: Actor):
    return service.messages(str(conversation_id), actor)


@router.post('/conversations/{conversation_id}/messages', response_model=Answer)
def ask(conversation_id: UUID, payload: MessageCreate, service: Service, actor: Actor):
    return service.ask(str(conversation_id), actor, payload)


@router.delete('/conversations/{conversation_id}', status_code=204)
def delete(conversation_id: UUID, service: Service, actor: Actor):
    service.delete(str(conversation_id), actor)
    return Response(status_code=204)
