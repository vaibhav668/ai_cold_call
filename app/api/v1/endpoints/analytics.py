from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
from app.db.session import get_db_session
from app.api.deps import get_current_user
from app.services.analytics_service import AnalyticsService
from app.models.user import User

router = APIRouter()

@router.get("/analytics/summary")
async def get_platform_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Retrieve global KPIs and call stats summaries across the platform."""
    service = AnalyticsService(db)
    return await service.get_global_summary()

@router.get("/analytics/campaigns/{campaign_id}")
async def get_campaign_stats(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Retrieve call reports and success outcomes aggregates for a campaign."""
    service = AnalyticsService(db)
    return await service.get_campaign_analytics(campaign_id)
