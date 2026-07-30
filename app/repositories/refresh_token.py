from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.models.refresh_token import RefreshToken
from app.repositories.base import BaseRepository

class RefreshTokenRepository(BaseRepository[RefreshToken]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(RefreshToken, db_session)

    async def get_by_token(self, token: str) -> Optional[RefreshToken]:
        """Fetch a refresh token record by its unique string token."""
        query = select(self.model).where(self.model.token == token)
        result = await self.db_session.execute(query)
        return result.scalars().first()
