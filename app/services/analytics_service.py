import uuid
from typing import Dict, Any
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.campaign import Campaign
from app.models.call_log import CallLog
from app.models.campaign_lead import CampaignLead
from app.repositories.campaign import CampaignRepository
from app.core.exceptions import NotFoundException

class AnalyticsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.campaign_repo = CampaignRepository(db)

    async def get_campaign_analytics(self, campaign_id: uuid.UUID) -> Dict[str, Any]:
        """Calculate call statistics and outcomes metrics for a specific campaign."""
        campaign = await self.campaign_repo.get(campaign_id)
        if not campaign:
            raise NotFoundException("Campaign not found.")
            
        total_q = select(func.count(CallLog.id)).where(CallLog.campaign_id == campaign_id)
        total_result = await self.db.execute(total_q)
        total_calls = total_result.scalar_one() or 0
        
        completed_q = select(func.count(CallLog.id)).where(
            and_(
                CallLog.campaign_id == campaign_id,
                CallLog.status == "completed"
            )
        )
        completed_result = await self.db.execute(completed_q)
        completed_calls = completed_result.scalar_one() or 0
        
        failed_q = select(func.count(CallLog.id)).where(
            and_(
                CallLog.campaign_id == campaign_id,
                CallLog.status == "failed"
            )
        )
        failed_result = await self.db.execute(failed_q)
        failed_calls = failed_result.scalar_one() or 0
        
        avg_dur_q = select(func.avg(CallLog.duration_seconds)).where(CallLog.campaign_id == campaign_id)
        avg_dur_result = await self.db.execute(avg_dur_q)
        avg_duration = float(avg_dur_result.scalar_one() or 0.0)
        
        status_q = select(CampaignLead.status, func.count(CampaignLead.id)).where(
            CampaignLead.campaign_id == campaign_id
        ).group_by(CampaignLead.status)
        status_result = await self.db.execute(status_q)
        lead_status_distribution = {row[0]: row[1] for row in status_result.all()}
        
        success_rate = 0.0
        if total_calls > 0:
            success_rate = round((completed_calls / total_calls) * 100.0, 2)
            
        return {
            "campaign_id": str(campaign_id),
            "campaign_name": campaign.name,
            "total_calls": total_calls,
            "completed_calls": completed_calls,
            "failed_calls": failed_calls,
            "success_rate_percentage": success_rate,
            "average_duration_seconds": round(avg_duration, 2),
            "lead_status_distribution": lead_status_distribution
        }

    async def get_global_summary(self) -> Dict[str, Any]:
        """Aggregate calls performance indicators across the entire platform."""
        camp_q = select(func.count(Campaign.id))
        camp_result = await self.db.execute(camp_q)
        total_campaigns = camp_result.scalar_one() or 0
        
        active_camp_q = select(func.count(Campaign.id)).where(Campaign.status == "active")
        active_camp_result = await self.db.execute(active_camp_q)
        active_campaigns = active_camp_result.scalar_one() or 0
        
        calls_q = select(func.count(CallLog.id))
        calls_result = await self.db.execute(calls_q)
        total_calls = calls_result.scalar_one() or 0
        
        completed_q = select(func.count(CallLog.id)).where(CallLog.status == "completed")
        completed_result = await self.db.execute(completed_q)
        completed_calls = completed_result.scalar_one() or 0
        
        avg_dur_q = select(func.avg(CallLog.duration_seconds))
        avg_dur_result = await self.db.execute(avg_dur_q)
        avg_duration = float(avg_dur_result.scalar_one() or 0.0)
        
        success_rate = 0.0
        if total_calls > 0:
            success_rate = round((completed_calls / total_calls) * 100.0, 2)
            
        return {
            "total_campaigns": total_campaigns,
            "active_campaigns": active_campaigns,
            "total_calls": total_calls,
            "completed_calls": completed_calls,
            "success_rate_percentage": success_rate,
            "average_duration_seconds": round(avg_duration, 2)
        }
