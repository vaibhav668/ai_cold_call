from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Tuple
from app.models.campaign import Campaign
from app.repositories.base import BaseRepository

class CampaignRepository(BaseRepository[Campaign]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Campaign, db_session)

    async def get_filtered(
        self,
        skip: int = 0,
        limit: int = 100,
        status: Optional[str] = None,
        workflow_type: Optional[str] = None
    ) -> Tuple[List[Campaign], int]:
        """Fetch campaigns with filters and pagination."""
        conditions = []
        if status:
            conditions.append(self.model.status == status)
        if workflow_type:
            conditions.append(self.model.workflow_type == workflow_type)
            
        query = select(self.model)
        if conditions:
            query = query.where(and_(*conditions))
            
        count_query = select(func.count()).select_from(query.subquery())
        count_result = await self.db_session.execute(count_query)
        total_count = count_result.scalar_one() or 0

        query = query.order_by(self.model.created_at.desc()).offset(skip).limit(limit)
        result = await self.db_session.execute(query)
        campaigns = list(result.scalars().all())
        
        return campaigns, total_count
