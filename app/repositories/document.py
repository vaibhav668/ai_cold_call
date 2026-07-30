import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.models.document import Document
from app.repositories.base import BaseRepository

class DocumentRepository(BaseRepository[Document]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Document, db_session)

    async def get_by_campaign(self, campaign_id: uuid.UUID) -> List[Document]:
        """Fetch all documents assigned to a specific campaign."""
        query = select(self.model).where(self.model.campaign_id == campaign_id).order_by(self.model.created_at.desc())
        result = await self.db_session.execute(query)
        return list(result.scalars().all())
