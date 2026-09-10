from app.core.exceptions import RAGNotConfiguredError
from app.rag.interfaces import Generator, Retriever
from app.schemas.rag import QueryRequest, QueryResponse


class RAGService:
    def __init__(
        self,
        retriever: Retriever | None = None,
        generator: Generator | None = None,
    ) -> None:
        self.retriever = retriever
        self.generator = generator

    async def query(self, request: QueryRequest) -> QueryResponse:
        if self.retriever is None or self.generator is None:
            raise RAGNotConfiguredError(
                "RAG retriever and generator are not configured."
            )

        documents = await self.retriever.retrieve(request.question, request.top_k)
        answer = await self.generator.generate(request.question, documents)
        return QueryResponse(answer=answer, sources=documents)
