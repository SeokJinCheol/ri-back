from fastapi import APIRouter

from app.api.v1 import chat, documents, health, indices, models, projects, rag, services

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(rag.router)

api_router.include_router(documents.router)

api_router.include_router(projects.router)

api_router.include_router(models.router)

api_router.include_router(indices.router)

api_router.include_router(services.router)

api_router.include_router(chat.router)
