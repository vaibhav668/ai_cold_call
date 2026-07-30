from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.models.call_log import CallLog
from app.repositories.base import BaseRepository

class CallLogRepository(BaseRepository[CallLog]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(CallLog, db_session)

    async def get_by_plivo_uuid(self, plivo_call_uuid: str) -> Optional[CallLog]:
        """Fetch a call log by its unique Plivo call UUID."""
        query = select(self.model).where(self.model.plivo_call_uuid == plivo_call_uuid)
        result = await self.db_session.execute(query)
        return result.scalars().first()
