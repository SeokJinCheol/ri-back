from fastapi import APIRouter

from app.api.v1 import documents, health, rag

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(rag.router)

api_router.include_router(documents.router)
