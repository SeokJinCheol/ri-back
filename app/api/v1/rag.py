from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_rag_service
from app.core.exceptions import RAGNotConfiguredError
from app.schemas.rag import QueryRequest, QueryResponse
from app.services.rag import RAGService

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post(
    "/query",
    response_model=QueryResponse,
    responses={503: {"description": "RAG providers are not configured"}},
)
async def query(
    request: QueryRequest,
    service: Annotated[RAGService, Depends(get_rag_service)],
) -> QueryResponse:
    try:
        return await service.query(request)
    except RAGNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
