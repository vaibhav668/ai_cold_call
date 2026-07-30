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
            import time
            import random
            logger.info(f"Initializing persistent ChromaDB client at: {settings.CHROMA_DB_PATH}")
            if settings.CHROMA_DB_PATH == ":memory:":
                self._client = chromadb.EphemeralClient()
                return

            for attempt in range(5):
                try:
                    self._client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)
                    logger.info("ChromaDB persistent client successfully connected.")
                    return
                except Exception as e:
                    err_msg = str(e).lower()
                    if "table collections already exists" in err_msg or "database is locked" in err_msg or "code: 1" in err_msg:
                        wait_time = random.uniform(0.5, 2.0)
                        logger.warning(f"ChromaDB initialization conflict (attempt {attempt+1}/5). Waiting {wait_time:.2f}s before retry. Error: {e}")
                        time.sleep(wait_time)
                    else:
                        raise e
            # Final fallback
            self._client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH)

    def get_client(self) -> Any:
        """Retrieve the active ChromaDB client, initializing it if necessary."""
        if not self._client:
            self.connect()
        return self._client

chroma_manager = ChromaManager()
