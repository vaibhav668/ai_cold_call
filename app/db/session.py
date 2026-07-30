from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.config import settings
from app.core.logging import logger
import ssl

# Create async engine with robust pool configuration and programmatic SSL logic
connect_args = {}
if "supabase.co" in settings.DATABASE_URL or "neon.tech" in settings.DATABASE_URL:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    connect_args = {"ssl": ctx}

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG and not settings.is_production,
    pool_pre_ping=True,  # Checks connection liveness before checking it out
    pool_size=10,        # Standard connections to keep open in the pool
    max_overflow=20,     # Max extra connections beyond pool_size
    connect_args=connect_args,
)

# Async session maker
async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency injection generator for async database sessions."""
    async with async_session_maker() as session:
        try:
            yield session
        except Exception as e:
            logger.error(f"Database session error occurred: {e}")
            await session.rollback()
            raise
        finally:
            await session.close()
