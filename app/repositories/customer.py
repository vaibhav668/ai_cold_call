from sqlalchemy import select, or_, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Tuple
from app.models.customer import Customer
from app.repositories.base import BaseRepository

class CustomerRepository(BaseRepository[Customer]):
    def __init__(self, db_session: AsyncSession):
        super().__init__(Customer, db_session)

    async def get_by_phone(self, phone_number: str) -> Optional[Customer]:
        """Fetch a customer record by phone number."""
        query = select(self.model).where(self.model.phone_number == phone_number)
        result = await self.db_session.execute(query)
        return result.scalars().first()

    async def get_filtered(
        self,
        skip: int = 0,
        limit: int = 100,
        search_query: Optional[str] = None,
        is_active: Optional[bool] = None
    ) -> Tuple[List[Customer], int]:
        """
        Fetch customers matching search_query (matching name or phone) and is_active filter.
        Returns a tuple of (list of customers, total count).
        """
        conditions = []
        
        if search_query:
            search_pattern = f"%{search_query}%"
            conditions.append(
                or_(
                    self.model.first_name.ilike(search_pattern),
                    self.model.last_name.ilike(search_pattern),
                    self.model.phone_number.ilike(search_pattern),
                    self.model.email.ilike(search_pattern)
                )
            )
            
        if is_active is not None:
            conditions.append(self.model.is_active == is_active)
            
        query = select(self.model)
        if conditions:
            query = query.where(and_(*conditions))
            
        # Count total matches
        count_query = select(func.count()).select_from(query.subquery())
        count_result = await self.db_session.execute(count_query)
        total_count = count_result.scalar_one() or 0

        # Apply pagination and order by created_at desc
        query = query.order_by(self.model.created_at.desc()).offset(skip).limit(limit)
        result = await self.db_session.execute(query)
        customers = list(result.scalars().all())
        
        return customers, total_count
