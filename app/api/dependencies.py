from functools import lru_cache

from app.services.rag import RAGService


@lru_cache
def get_rag_service() -> RAGService:
    # Inject concrete retriever/generator implementations here.
    # Manage network clients with FastAPI lifespan when providers are added.
    return RAGService()
