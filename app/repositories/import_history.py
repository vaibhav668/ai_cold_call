from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.models.import_history import ImportHistory
from app.repositories.base import BaseRepository

class ImportHistoryRepository(BaseRepository[ImportHistory]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(ImportHistory, db_session)

    async def get_ordered_history(self, skip: int = 0, limit: int = 100) -> List[ImportHistory]:
        """Fetch import history logs sorted newest first."""
        query = select(self.model).order_by(self.model.created_at.desc()).offset(skip).limit(limit)
        result = await self.db_session.execute(query)
        return list(result.scalars().all())
