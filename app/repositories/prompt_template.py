import uuid
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.models.prompt_template import PromptTemplate
from app.repositories.base import BaseRepository

class PromptTemplateRepository(BaseRepository[PromptTemplate]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(PromptTemplate, db_session)

    async def get_by_campaign(self, campaign_id: uuid.UUID) -> List[PromptTemplate]:
        """Fetch all templates for a specific campaign."""
        query = select(self.model).where(self.model.campaign_id == campaign_id).order_by(self.model.created_at.desc())
        result = await self.db_session.execute(query)
        return list(result.scalars().all())

    async def get_active_by_campaign(self, campaign_id: uuid.UUID) -> Optional[PromptTemplate]:
        """Fetch the active prompt template config configured for a campaign."""
        query = select(self.model).where(
            and_(
                self.model.campaign_id == campaign_id,
                self.model.is_active == True
            )
        )
        result = await self.db_session.execute(query)
        return result.scalars().first()
