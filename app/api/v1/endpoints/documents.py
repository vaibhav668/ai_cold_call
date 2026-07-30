from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
import uuid
from app.db.session import get_db_session
from app.api.deps import get_current_user, RoleChecker
from app.schemas.document import DocumentOut, SearchResultOut
from app.services.document_service import DocumentService
from app.services.rag_service import RAGService
from app.models.user import User

router = APIRouter()

@router.post("/campaigns/{campaign_id}/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_campaign_document(
    campaign_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Upload a TXT or PDF knowledge document to be indexed for a campaign's RAG agent."""
    file_bytes = await file.read()
    doc_service = DocumentService(db)
    
    return await doc_service.upload_document(
        campaign_id=campaign_id,
        filename=file.filename,
        content_bytes=file_bytes
    )

@router.get("/campaigns/{campaign_id}/documents", response_model=List[DocumentOut])
async def list_campaign_documents(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Retrieve all uploaded documents configured for a campaign."""
    doc_service = DocumentService(db)
    return await doc_service.list_campaign_documents(campaign_id)

@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Delete an uploaded knowledge document and clear its vectors in Qdrant."""
    doc_service = DocumentService(db)
    await doc_service.delete_document(document_id)

@router.get("/campaigns/{campaign_id}/search", response_model=SearchResultOut)
async def semantic_search_knowledge(
    campaign_id: uuid.UUID,
    q: str,
    limit: int = 5,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Execute a semantic RAG search query matching chunks within a campaign knowledge context."""
    rag_service = RAGService()
    results = await rag_service.search_knowledge(
        campaign_id=campaign_id,
        query=q,
        limit=limit
    )
    return {
        "query": q,
        "results": results
    }
