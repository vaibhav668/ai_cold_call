import io
import uuid
import pypdf
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.document import DocumentRepository
from app.repositories.campaign import CampaignRepository
from app.models.document import Document
from app.services.rag_service import RAGService
from app.core.exceptions import NotFoundException, AppException
from app.core.logging import logger

class DocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.doc_repo = DocumentRepository(db)
        self.campaign_repo = CampaignRepository(db)
        self.rag_service = RAGService()

    async def upload_document(
        self,
        campaign_id: uuid.UUID,
        filename: str,
        content_bytes: bytes
    ) -> Document:
        """Extract text from TXT/PDF, indexes chunks in Qdrant, and saves metadata in database."""
        campaign = await self.campaign_repo.get(campaign_id)
        if not campaign:
            raise NotFoundException("Campaign not found.")
            
        ext = filename.split(".")[-1].lower()
        extracted_text = ""
        
        if ext == "txt":
            try:
                extracted_text = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                extracted_text = content_bytes.decode("latin-1")
        elif ext == "pdf":
            try:
                reader = pypdf.PdfReader(io.BytesIO(content_bytes))
                text_list = []
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_list.append(page_text)
                extracted_text = "\n".join(text_list)
            except Exception as e:
                logger.error(f"Failed to parse PDF text: {e}")
                raise AppException(f"Invalid PDF file parsing error: {str(e)}", 400)
        else:
            raise AppException("Unsupported document format. Only .txt and .pdf files are supported.", 400)
            
        if not extracted_text.strip():
            raise AppException("Uploaded file contains no readable text content.", 400)
            
        doc = Document(
            campaign_id=campaign_id,
            filename=filename,
            file_type=ext,
            status="processing",
            total_chunks=0
        )
        created_doc = await self.doc_repo.create(doc)
        await self.db.commit()
        
        try:
            chunks_count = await self.rag_service.index_document(
                campaign_id=campaign_id,
                document_id=created_doc.id,
                filename=filename,
                text=extracted_text
            )
            
            await self.doc_repo.update(
                created_doc,
                {
                    "status": "active",
                    "total_chunks": chunks_count
                }
            )
            await self.db.commit()
        except Exception as e:
            logger.error(f"Indexing document failed: {e}")
            await self.doc_repo.update(created_doc, {"status": "failed"})
            await self.db.commit()
            raise AppException(f"Vector indexing processing failed: {str(e)}", 500)
            
        return created_doc

    async def delete_document(self, document_id: uuid.UUID) -> None:
        """Remove document record from database and purge vectors in Qdrant."""
        doc = await self.doc_repo.get(document_id)
        if not doc:
            raise NotFoundException("Document not found.")
            
        await self.rag_service.delete_document_vectors(document_id)
        await self.doc_repo.remove(document_id)
        await self.db.commit()

    async def list_campaign_documents(self, campaign_id: uuid.UUID) -> List[Document]:
        """Fetch all documents attached to a campaign."""
        campaign = await self.campaign_repo.get(campaign_id)
        if not campaign:
            raise NotFoundException("Campaign not found.")
        return await self.doc_repo.get_by_campaign(campaign_id)
