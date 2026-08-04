from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.voice_demo.models.voice_profile import VoiceProfile
from app.repositories.base import BaseRepository

class VoiceProfileRepository(BaseRepository[VoiceProfile]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(VoiceProfile, db_session)

    async def get_active(self) -> List[VoiceProfile]:
        """Fetch all active voice profiles."""
        query = select(self.model).where(self.model.status == "active")
        result = await self.db_session.execute(query)
        return list(result.scalars().all())
