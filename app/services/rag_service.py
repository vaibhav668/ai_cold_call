import uuid
from typing import List, Dict, Any
from app.db.chroma import chroma_manager
from app.services.embedding_service import EmbeddingService
from app.core.logging import logger

COLLECTION_NAME = "knowledge_base"

def chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> List[str]:
    """Split text into overlapping character-based chunks."""
    chunks = []
    start = 0
    text_len = len(text)
    
    if text_len == 0:
        return []
        
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end]
        chunks.append(chunk)
        
        start += chunk_size - chunk_overlap
        if chunk_size <= chunk_overlap:
            break
            
    return chunks

class RAGService:
    """
    RAG Service for document chunking, indexing, and retrieval.
    Decoupled from specific embedding providers via the EmbeddingService facade.
    """

    def __init__(self) -> None:
        self.embedding_service = EmbeddingService()

    async def get_collection_name(self) -> str:
        return COLLECTION_NAME

    async def initialize_collection(self) -> None:
        """Create ChromaDB collection if not already initialized. Verify dimensionality."""
        client = chroma_manager.get_client()
        try:
            collection = client.get_or_create_collection(name=COLLECTION_NAME)
            
            # Check for embedding dimensionality conflict (e.g. migrating 1536 -> 1024)
            # Try a dummy query with the provider's native dimension size
            dummy_vector = [0.0] * self.embedding_service.dimension
            try:
                collection.query(query_embeddings=[dummy_vector], n_results=1)
            except Exception as dim_err:
                err_str = str(dim_err).lower()
                if "dimension" in err_str or "dimensionality" in err_str or "size" in err_str:
                    logger.warning(
                        f"[RAG] ChromaDB dimension mismatch: {dim_err}. "
                        "Recreating collection to adapt to new embedding model..."
                    )
                    try:
                        client.delete_collection(name=COLLECTION_NAME)
                        client.get_or_create_collection(name=COLLECTION_NAME)
                        logger.info("[RAG] ChromaDB collection recreated successfully with 1024-d.")
                    except Exception as del_err:
                        logger.error(f"[RAG] Failed to delete/recreate collection: {del_err}")
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB collection: {e}")

    async def index_document(
        self,
        campaign_id: uuid.UUID,
        document_id: uuid.UUID,
        filename: str,
        text: str
    ) -> int:
        """Chunks document text, generates embeddings, and upserts points to ChromaDB."""
        client = chroma_manager.get_client()
        
        chunks = chunk_text(text)
        if not chunks:
            return 0
            
        logger.info(f"Indexing document '{filename}' into ChromaDB with {len(chunks)} chunks...")
        embeddings = await self.embedding_service.get_embeddings(chunks)
        
        collection = client.get_or_create_collection(name=COLLECTION_NAME)
        
        ids = []
        metadatas = []
        documents = []
        
        for idx, (chunk, vector) in enumerate(zip(chunks, embeddings)):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{document_id}_{idx}"))
            ids.append(point_id)
            metadatas.append({
                "campaign_id": str(campaign_id),
                "document_id": str(document_id),
                "filename": filename,
                "chunk_index": idx
            })
            documents.append(chunk)
            
        try:
            collection.add(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents
            )
            return len(chunks)
        except Exception as e:
            logger.error(f"Failed to upsert points to ChromaDB: {e}")
            return len(chunks)

    async def search_knowledge(
        self,
        campaign_id: uuid.UUID,
        query: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Perform semantic search on ChromaDB knowledge base filtered by campaign metadata."""
        client = chroma_manager.get_client()
        query_vector = await self.embedding_service.get_query_embedding(query)
        
        try:
            collection = client.get_or_create_collection(name=COLLECTION_NAME)
            results = collection.query(
                query_embeddings=[query_vector],
                n_results=limit,
                where={"campaign_id": str(campaign_id)}
            )
            
            formatted_results = []
            if results and "documents" in results and results["documents"]:
                documents = results["documents"][0]
                ids = results["ids"][0]
                metadatas = results["metadatas"][0]
                distances = results.get("distances", [[]])[0]
                
                for idx, (doc, doc_id, meta) in enumerate(zip(documents, ids, metadatas)):
                    score = 1.0 - distances[idx] if idx < len(distances) else 1.0
                    formatted_results.append({
                        "text": doc,
                        "score": score,
                        "document_id": uuid.UUID(meta.get("document_id")) if meta.get("document_id") else uuid.uuid4(),
                        "filename": meta.get("filename", ""),
                        "chunk_index": meta.get("chunk_index", 0)
                    })
            return formatted_results
        except Exception as e:
            logger.error(f"Failed to execute semantic search in ChromaDB: {e}")
            return []

    async def delete_document_vectors(self, document_id: uuid.UUID) -> None:
        """Purge vectors from ChromaDB for a specific document."""
        client = chroma_manager.get_client()
        try:
            collection = client.get_or_create_collection(name=COLLECTION_NAME)
            collection.delete(where={"document_id": str(document_id)})
            logger.info(f"Purged ChromaDB vectors for document ID {document_id}")
        except Exception as e:
            logger.error(f"Failed to delete document vectors from ChromaDB: {e}")
