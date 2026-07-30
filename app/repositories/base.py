from typing import Any, Dict, Generic, List, Optional, Type, TypeVar
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)

class BaseRepository(Generic[ModelType]):
    def __init__(self, model: Type[ModelType], db_session: AsyncSession):
        self.model = model
        self.db_session = db_session

    async def get(self, id: Any) -> Optional[ModelType]:
        """Fetch a single record by its ID."""
        return await self.db_session.get(self.model, id)

    async def get_multi(self, skip: int = 0, limit: int = 100) -> List[ModelType]:
        """Fetch multiple records with offset and limit pagination."""
        query = select(self.model).offset(skip).limit(limit)
        result = await self.db_session.execute(query)
        return list(result.scalars().all())

    async def create(self, obj_in: Dict[str, Any] | ModelType) -> ModelType:
        """Create a new record."""
        if isinstance(obj_in, self.model):
            db_obj = obj_in
        else:
            db_obj = self.model(**obj_in)
        self.db_session.add(db_obj)
        await self.db_session.flush()
        return db_obj

    async def update(self, db_obj: ModelType, obj_in: Dict[str, Any]) -> ModelType:
        """Update an existing record's attributes."""
        for field, value in obj_in.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)
        self.db_session.add(db_obj)
        await self.db_session.flush()
        return db_obj

    async def remove(self, id: Any) -> Optional[ModelType]:
        """Remove a record by its ID."""
        obj = await self.get(id)
        if obj:
            await self.db_session.delete(obj)
            await self.db_session.flush()
        return obj
