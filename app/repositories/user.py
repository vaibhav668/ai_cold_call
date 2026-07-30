from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.models.user import User
from app.repositories.base import BaseRepository

class UserRepository(BaseRepository[User]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(User, db_session)

    async def get_by_email(self, email: str) -> Optional[User]:
        """Fetch a user by email address."""
        query = select(self.model).where(self.model.email == email)
        result = await self.db_session.execute(query)
        return result.scalars().first()
