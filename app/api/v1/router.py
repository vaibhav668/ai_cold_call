from fastapi import APIRouter, Depends, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db_session
from app.db.chroma import chroma_manager
from app.core.logging import logger

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.users import router as users_router
from app.api.v1.endpoints.customers import router as customers_router
from app.api.v1.endpoints.campaigns import router as campaigns_router
from app.api.v1.endpoints.documents import router as documents_router
from app.api.v1.endpoints.prompts import router as prompts_router
from app.api.v1.endpoints.conversation import router as conversation_router
from app.api.v1.endpoints.telephony import router as telephony_router
from app.api.v1.endpoints.analytics import router as analytics_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
router.include_router(users_router, prefix="/users", tags=["Users"])
router.include_router(customers_router, prefix="/customers", tags=["Customers"])
router.include_router(campaigns_router, prefix="/campaigns", tags=["Campaigns"])
router.include_router(documents_router, tags=["Knowledge Base (RAG)"])
router.include_router(prompts_router, tags=["Prompt Management"])
router.include_router(conversation_router, tags=["Conversation Engine"])
router.include_router(telephony_router, tags=["Telephony"])
router.include_router(analytics_router, tags=["Analytics"])

@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check(db: AsyncSession = Depends(get_db_session)):
    """Verifies backend application status and database connectivity (PostgreSQL, ChromaDB)."""
    db_status = "unhealthy"
    chromadb_status = "unhealthy"
    
    # 1. Test PostgreSQL
    try:
        await db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        logger.error(f"Database health check query failed: {e}")
        
    # 2. Test ChromaDB
    try:
        if chroma_manager.get_client().heartbeat() is not None:
            chromadb_status = "healthy"
    except Exception as e:
        logger.error(f"ChromaDB health check failed: {e}")
        
    overall_status = "healthy"
    if "unhealthy" in (db_status, chromadb_status):
        overall_status = "degraded"
        
    return {
        "status": overall_status,
        "services": {
            "database": db_status,
            "chromadb": chromadb_status
        }
    }

from fastapi.responses import JSONResponse

@router.get("/health/database", status_code=status.HTTP_200_OK)
async def database_health_check(db: AsyncSession = Depends(get_db_session)):
    """Verifies detailed database connectivity (reachable, engine healthy, session creation, SELECT 1 query)."""
    db_reachable = False
    engine_healthy = False
    session_created = False
    query_executed = False
    error_detail = None
    status_code = status.HTTP_200_OK

    try:
        # 1. Verify SQLAlchemy engine is instantiated
        from app.db.session import get_engine
        if get_engine() is not None:
            engine_healthy = True

        # 2. Verify session creation
        if db is not None:
            session_created = True

        # 3. Verify SELECT 1 query executes & database is reachable
        await db.execute(text("SELECT 1"))
        db_reachable = True
        query_executed = True
        overall_status = "healthy"
    except Exception as e:
        logger.error(f"Detailed database health check failed: {e}")
        error_detail = str(e)
        overall_status = "unhealthy"
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall_status,
            "checks": {
                "database_reachable": db_reachable,
                "engine_healthy": engine_healthy,
                "session_created": session_created,
                "query_executed": query_executed
            },
            "error": error_detail
        }
    )
