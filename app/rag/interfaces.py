from typing import Protocol

from app.schemas.rag import SourceDocument


class Retriever(Protocol):
    async def retrieve(self, question: str, top_k: int) -> list[SourceDocument]:
        """Embed a query and retrieve relevant chunks from a vector store."""
        ...


class Generator(Protocol):
    async def generate(self, question: str, documents: list[SourceDocument]) -> str:
        """Generate an answer grounded in the retrieved context."""
        ...
