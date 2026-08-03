from typing import List
from app.core.config import settings
from app.services.embeddings.base import EmbeddingProvider
from app.services.embeddings.bge_m3_provider import BGEM3EmbeddingProvider

class EmbeddingService(EmbeddingProvider):
    """
    Facade class representing the Embedding generation service.
    RAG, indexing, and document query systems use this facade to generate vectors.
    """

    def __init__(self) -> None:
        provider_name = settings.EMBEDDING_PROVIDER.lower()
        if provider_name == "bge_m3":
            self.provider: EmbeddingProvider = BGEM3EmbeddingProvider()
        else:
            self.provider: EmbeddingProvider = BGEM3EmbeddingProvider()

    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        return await self.provider.get_embeddings(texts)

    async def get_query_embedding(self, text: str) -> List[float]:
        return await self.provider.get_query_embedding(text)

    @property
    def dimension(self) -> int:
        return self.provider.dimension


class MockEmbeddingService(EmbeddingProvider):
    """Mock embedding provider generating 1024-d vectors for tests."""

    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        import random
        results = []
        for text in texts:
            random.seed(len(text))
            results.append([random.uniform(-1, 1) for _ in range(1024)])
        return results

    async def get_query_embedding(self, text: str) -> List[float]:
        embeddings = await self.get_embeddings([text])
        return embeddings[0]

    @property
    def dimension(self) -> int:
        return 1024
