import uuid
from datetime import datetime, timezone
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.models.campaign_lead import CampaignLead
from app.repositories.base import BaseRepository

class CampaignLeadRepository(BaseRepository[CampaignLead]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(CampaignLead, db_session)

    async def get_by_campaign_and_customer(self, campaign_id: uuid.UUID, customer_id: uuid.UUID) -> Optional[CampaignLead]:
        """Fetch a CampaignLead record by campaign and customer IDs."""
        query = select(self.model).where(
            and_(
                self.model.campaign_id == campaign_id,
                self.model.customer_id == customer_id
            )
        )
        result = await self.db_session.execute(query)
        return result.scalars().first()

    async def get_next_dialable_leads(
        self,
        campaign_id: uuid.UUID,
        max_retries: int,
        limit: int = 10
    ) -> List[CampaignLead]:
        """
        Fetch the next batch of dialable leads for a campaign.
        Retrieves leads that are either 'pending' or 'retry_scheduled' where next_attempt_at is in the past,
        given they haven't exceeded the max retries limit.
        """
        now = datetime.now(timezone.utc)
        
        is_pending = self.model.status == "pending"
        is_retryable = and_(
            self.model.status == "retry_scheduled",
            self.model.next_attempt_at <= now,
            self.model.retry_count < max_retries
        )
        
        query = select(self.model).where(
            and_(
                self.model.campaign_id == campaign_id,
                or_(is_pending, is_retryable)
            )
        ).order_by(
            self.model.status.desc(), 
            self.model.created_at.asc()
        ).limit(limit)
        
        result = await self.db_session.execute(query)
        return list(result.scalars().all())
