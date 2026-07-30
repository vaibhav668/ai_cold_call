import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.campaign import CampaignRepository
from app.repositories.campaign_lead import CampaignLeadRepository
from app.repositories.customer import CustomerRepository
from app.models.campaign_lead import CampaignLead
from app.core.exceptions import NotFoundException

class CampaignService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.campaign_repo = CampaignRepository(db)
        self.lead_repo = CampaignLeadRepository(db)
        self.customer_repo = CustomerRepository(db)

    async def assign_leads(self, campaign_id: uuid.UUID, customer_ids: List[uuid.UUID]) -> Dict[str, Any]:
        """Assign list of customers to a campaign, preventing duplicates."""
        campaign = await self.campaign_repo.get(campaign_id)
        if not campaign:
            raise NotFoundException("Campaign not found.")
            
        success_count = 0
        skip_count = 0
        
        for cust_id in customer_ids:
            customer = await self.customer_repo.get(cust_id)
            if not customer:
                skip_count += 1
                continue
                
            existing = await self.lead_repo.get_by_campaign_and_customer(campaign_id, cust_id)
            if existing:
                skip_count += 1
                continue
                
            lead = CampaignLead(
                campaign_id=campaign_id,
                customer_id=cust_id,
                status="pending",
                retry_count=0
            )
            await self.lead_repo.create(lead)
            success_count += 1
            
        await self.db.commit()
        return {
            "total_requested": len(customer_ids),
            "successfully_assigned": success_count,
            "skipped_or_invalid": skip_count
        }

    async def select_next_leads(self, campaign_id: uuid.UUID, limit: int = 10) -> List[CampaignLead]:
        """Retrieve next dialing queue leads if campaign schedule windows allow it."""
        campaign = await self.campaign_repo.get(campaign_id)
        if not campaign:
            raise NotFoundException("Campaign not found.")
            
        now = datetime.now(timezone.utc)
        
        if campaign.status == "scheduled":
            if campaign.scheduled_start and now >= campaign.scheduled_start.replace(tzinfo=timezone.utc):
                campaign.status = "active"
                await self.campaign_repo.update(campaign, {"status": "active"})
                await self.db.commit()
                
        if campaign.status == "active" and campaign.scheduled_end:
            if now >= campaign.scheduled_end.replace(tzinfo=timezone.utc):
                campaign.status = "completed"
                await self.campaign_repo.update(campaign, {"status": "completed"})
                await self.db.commit()
                
        if campaign.status != "active":
            return []
            
        return await self.lead_repo.get_next_dialable_leads(
            campaign_id=campaign_id,
            max_retries=campaign.max_retries,
            limit=limit
        )

    async def record_call_outcome(
        self,
        campaign_id: uuid.UUID,
        customer_id: uuid.UUID,
        outcome: str
    ) -> CampaignLead:
        """Process dial attempt updates, scheduling retries if needed."""
        campaign = await self.campaign_repo.get(campaign_id)
        if not campaign:
            raise NotFoundException("Campaign not found.")
            
        lead = await self.lead_repo.get_by_campaign_and_customer(campaign_id, customer_id)
        if not lead:
            raise NotFoundException("Customer lead not registered in this campaign.")
            
        now = datetime.now(timezone.utc)
        
        if outcome == "completed":
            update_data = {
                "status": "completed",
                "last_attempt_at": now,
                "next_attempt_at": None
            }
        else:
            next_retry_count = lead.retry_count + 1
            if next_retry_count < campaign.max_retries:
                next_try = now + timedelta(minutes=campaign.retry_interval_minutes)
                update_data = {
                    "status": "retry_scheduled",
                    "retry_count": next_retry_count,
                    "last_attempt_at": now,
                    "next_attempt_at": next_try
                }
            else:
                update_data = {
                    "status": "failed",
                    "retry_count": next_retry_count,
                    "last_attempt_at": now,
                    "next_attempt_at": None
                }
                
        updated_lead = await self.lead_repo.update(lead, update_data)
        await self.db.commit()
        return updated_lead
