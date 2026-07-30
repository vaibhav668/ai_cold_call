import uuid
from typing import List, Dict, Any
from app.db.chroma import chroma_manager
from app.services.embedding_service import OpenAIEmbeddingService
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
    def __init__(self) -> None:
        self.embedding_service = OpenAIEmbeddingService()

    async def get_collection_name(self) -> str:
        return COLLECTION_NAME

    async def initialize_collection(self) -> None:
        """Create ChromaDB collection if not already initialized."""
        client = chroma_manager.get_client()
        try:
            client.get_or_create_collection(name=COLLECTION_NAME)
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB collection: {e}")
            pass

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
            # ChromaDB expects embeddings as float lists
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
