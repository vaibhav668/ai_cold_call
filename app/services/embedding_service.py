from abc import ABC, abstractmethod
from typing import List
import openai
from app.core.config import settings
from app.core.logging import logger

class EmbeddingService(ABC):
    @abstractmethod
    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate vector embeddings for a list of string chunks."""
        pass

    @abstractmethod
    async def get_query_embedding(self, text: str) -> List[float]:
        """Generate vector embedding for a single search query."""
        pass

class OpenAIEmbeddingService(EmbeddingService):
    def __init__(self) -> None:
        self.api_key = settings.OPENAI_API_KEY
        self.model = "text-embedding-3-small"
        self.client = openai.AsyncOpenAI(api_key=self.api_key)

    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        if not self.api_key or self.api_key == "test_openai_key":
            logger.warning("OpenAI API key missing or placeholder. Falling back to mock embeddings...")
            mock = MockEmbeddingService()
            return await mock.get_embeddings(texts)
            
        try:
            response = await self.client.embeddings.create(
                input=texts,
                model=self.model
            )
            return [data.embedding for data in response.data]
        except Exception as e:
            logger.warning(f"OpenAI embedding generation failed: {e}. Falling back to mock embeddings.")
            mock = MockEmbeddingService()
            return await mock.get_embeddings(texts)

    async def get_query_embedding(self, text: str) -> List[float]:
        embeddings = await self.get_embeddings([text])
        return embeddings[0]

class MockEmbeddingService(EmbeddingService):
    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        import random
        results = []
        for text in texts:
            random.seed(len(text))
            results.append([random.uniform(-1, 1) for _ in range(1536)])
        return results

    async def get_query_embedding(self, text: str) -> List[float]:
        embeddings = await self.get_embeddings([text])
        return embeddings[0]
