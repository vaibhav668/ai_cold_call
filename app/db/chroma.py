from typing import Any
import chromadb
from app.core.config import settings
from app.core.logging import logger

class ChromaManager:
    def __init__(self) -> None:
        self._client = None

    def connect(self) -> None:
        """Initialize ChromaDB Persistent Client."""
        if not self._client:
            logger.info(f"Initializing persistent ChromaDB client at: {settings.CHROMA_DB_PATH}")
            if settings.CHROMA_DB_PATH == ":memory:":
                self._client = chromadb.EphemeralClient()
            else:
                self._client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)

    def get_client(self) -> Any:
        """Retrieve the active ChromaDB client, initializing it if necessary."""
        if not self._client:
            self.connect()
        return self._client

chroma_manager = ChromaManager()
