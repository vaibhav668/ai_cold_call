import asyncio
from typing import List
from app.core.logging import logger
from app.services.embeddings.base import EmbeddingProvider

class BGEM3EmbeddingProvider(EmbeddingProvider):
    """
    Multilingual embedding provider using the BAAI/bge-m3 model.
    Generates 1024-dimensional vectors.
    """

    _model_instance = None
    _model_lock = asyncio.Lock()

    @classmethod
    async def _get_model(cls):
        """Loads and caches the SentenceTransformer model instance as a singleton."""
        if cls._model_instance is not None:
            return cls._model_instance

        async with cls._model_lock:
            if cls._model_instance is not None:
                return cls._model_instance

            try:
                from sentence_transformers import SentenceTransformer
                logger.info("[EMBEDDINGS] Initializing BAAI/bge-m3 model locally (CPU)...")
                # Run blocking load inside executor
                def load():
                    return SentenceTransformer("BAAI/bge-m3", device="cpu")
                
                cls._model_instance = await asyncio.get_event_loop().run_in_executor(None, load)
                logger.info("[EMBEDDINGS] BAAI/bge-m3 model loaded successfully.")
            except Exception as e:
                logger.error(f"[EMBEDDINGS] Failed to initialize local bge-m3 model: {e}. Mock fallback enabled.")
                cls._model_instance = "FAILED"
            return cls._model_instance

    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        # 1. Try local SentenceTransformer model
        model = await self._get_model()
        if model != "FAILED" and model is not None:
            try:
                def encode_texts():
                    # Generate embeddings, returns list of numpy arrays
                    raw_vecs = model.encode(texts, normalize_embeddings=True)
                    return [vec.tolist() for vec in raw_vecs]

                return await asyncio.get_event_loop().run_in_executor(None, encode_texts)
            except Exception as e:
                logger.error(f"[EMBEDDINGS] Local embedding encoding failed: {e}")

        # 2. Mock fallback (1024-dimensional vectors)
        return self._mock_embeddings(texts)

    async def get_query_embedding(self, text: str) -> List[float]:
        embeddings = await self.get_embeddings([text])
        return embeddings[0]

    @property
    def dimension(self) -> int:
        return 1024

    def _mock_embeddings(self, texts: List[str]) -> List[List[float]]:
        import random
        logger.warning("[EMBEDDINGS] SentenceTransformer failed or not initialized. Returning 1024-d mock embeddings...")
        results = []
        for text in texts:
            # Deterministic mock based on text length for stable testing
            random.seed(len(text))
            results.append([random.uniform(-1.0, 1.0) for _ in range(1024)])
        return results
