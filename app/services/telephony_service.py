import uuid
import httpx
from typing import Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.logging import logger
from app.repositories.call_log import CallLogRepository
from app.repositories.campaign_lead import CampaignLeadRepository

class TelephonyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.call_log_repo = CallLogRepository(db)
        self.lead_repo = CampaignLeadRepository(db)

    async def initiate_call(
        self,
        campaign_id: uuid.UUID,
        customer_id: uuid.UUID,
        phone_number: str,
        callback_domain: str = "example.com"
    ) -> Tuple[str, str]:
        """Trigger outbound call via Plivo API, returning Plivo Call Request UUID and queuing status."""
        auth_id = settings.PLIVO_AUTH_ID
        auth_token = settings.PLIVO_AUTH_TOKEN
        from_phone = settings.PLIVO_PHONE_NUMBER
        
        answer_url = f"https://{callback_domain}/api/v1/telephony/answer?campaign_id={campaign_id}&customer_id={customer_id}"
        status_url = f"https://{callback_domain}/api/v1/telephony/status"
        
        call_log = await self.call_log_repo.create({
            "campaign_id": campaign_id,
            "customer_id": customer_id,
            "phone_number": phone_number,
            "status": "initiated",
            "plivo_call_uuid": f"pending-{uuid.uuid4()}",
            "duration_seconds": 0,
            "transcript": []
        })
        await self.db.commit()
        
        if not auth_id or auth_id == "test_auth_id" or not auth_token:
            logger.warning("Plivo credentials not configured. Initializing simulated Mock outbound call...")
            mock_uuid = f"mock-plivo-call-{uuid.uuid4()}"
            
            await self.call_log_repo.update(call_log, {
                "plivo_call_uuid": mock_uuid,
                "status": "ringing"
            })
            await self.db.commit()
            return mock_uuid, "queued"
            
        url = f"https://api.plivo.com/v1/Account/{auth_id}/Call/"
        payload = {
            "from": from_phone,
            "to": phone_number,
            "answer_url": answer_url,
            "answer_method": "POST",
            "callback_url": status_url,
            "callback_method": "POST"
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    url,
                    auth=(auth_id, auth_token),
                    json=payload
                )
                
                if response.status_code not in [200, 201, 202]:
                    logger.error(f"Plivo outbound trigger failed: {response.status_code} - {response.text}")
                    await self.call_log_repo.update(call_log, {"status": "failed"})
                    await self.db.commit()
                    return "", "failed"
                    
                data = response.json()
                request_uuid = data.get("request_uuid", "")
                
                await self.call_log_repo.update(call_log, {
                    "plivo_call_uuid": request_uuid,
                    "status": "ringing"
                })
                await self.db.commit()
                return request_uuid, "queued"
        except Exception as e:
            logger.error(f"Telephony service client exception: {e}")
            await self.call_log_repo.update(call_log, {"status": "failed"})
            await self.db.commit()
            return "", "failed"

    async def process_status_update(self, plivo_uuid: str, call_status: str, duration: int = 0) -> None:
        """Update Postgres CallLog and CampaignLead status records matching the Plivo call UUID."""
        query = select(self.call_log_repo.model).where(self.call_log_repo.model.plivo_call_uuid == plivo_uuid)
        result = await self.db.execute(query)
        call_log = result.scalars().first()
        
        if not call_log:
            logger.warning(f"Status callback received for untracked Call UUID: {plivo_uuid}")
            return
            
        mapped_status = call_status
        if call_status == "hangup" or call_status == "completed":
            mapped_status = "completed"
        elif call_status in ["failed", "no-answer", "busy", "rejected"]:
            mapped_status = "failed"
            
        await self.call_log_repo.update(call_log, {
            "status": mapped_status,
            "duration_seconds": duration
        })
        
        if call_log.campaign_id and call_log.customer_id:
            lead = await self.lead_repo.get_by_campaign_and_customer(call_log.campaign_id, call_log.customer_id)
            if lead:
                lead_status = "completed" if mapped_status == "completed" else "failed"
                await self.lead_repo.update(lead, {"status": lead_status})
                
        await self.db.commit()
        logger.info(f"Updated Call UUID {plivo_uuid} to database status: {mapped_status}")
