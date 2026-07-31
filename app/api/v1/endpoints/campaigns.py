from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import uuid
from app.db.session import get_db_session
from app.api.deps import get_current_user, RoleChecker
from app.schemas.campaign import CampaignOut, CampaignCreate, CampaignUpdate, CampaignPaginated, CampaignLeadOut, CampaignLeadAssign
from app.repositories.campaign import CampaignRepository
from app.services.campaign_service import CampaignService
from app.services.workflow_service import WorkflowService
from app.models.user import User

router = APIRouter()

@router.get("/", response_model=CampaignPaginated)
async def list_campaigns(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    workflow_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Retrieve campaigns list with optional filters and page limits."""
    campaign_repo = CampaignRepository(db)
    campaigns, total = await campaign_repo.get_filtered(
        skip=skip,
        limit=limit,
        status=status,
        workflow_type=workflow_type
    )
    return {
        "total": total,
        "items": campaigns,
        "skip": skip,
        "limit": limit
    }

@router.post("/", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    campaign_in: CampaignCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Create a new outbound call campaign configuration."""
    campaign_repo = CampaignRepository(db)
    
    # We must dump the schema data
    campaign_data = campaign_in.model_dump()
    db_obj = await campaign_repo.create(campaign_data)
    await db.commit()
    
    # Auto-seed workflow defaults script
    workflow_service = WorkflowService(db)
    await workflow_service.seed_campaign_defaults(db_obj.id, db_obj.workflow_type)
    await db.commit()
    
    if hasattr(db, "refresh"):
        await db.refresh(db_obj)
    return db_obj

@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Retrieve details of a single campaign configuration."""
    campaign_repo = CampaignRepository(db)
    campaign = await campaign_repo.get(campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found."
        )
    return campaign

@router.put("/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: uuid.UUID,
    campaign_in: CampaignUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Update configurations or status of an existing campaign."""
    campaign_repo = CampaignRepository(db)
    campaign = await campaign_repo.get(campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found."
        )
        
    update_data = campaign_in.model_dump(exclude_unset=True)
    updated_obj = await campaign_repo.update(campaign, update_data)
    await db.commit()
    if hasattr(db, "refresh"):
        await db.refresh(updated_obj)
    return updated_obj

@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Delete a campaign configuration from the database."""
    campaign_repo = CampaignRepository(db)
    campaign = await campaign_repo.get(campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found."
        )
    await campaign_repo.remove(campaign_id)
    await db.commit()

@router.post("/{campaign_id}/assign")
async def assign_leads_to_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignLeadAssign,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Assign a list of customer lead IDs to a campaign."""
    campaign_service = CampaignService(db)
    return await campaign_service.assign_leads(campaign_id, payload.customer_ids)

@router.get("/{campaign_id}/leads/next", response_model=List[CampaignLeadOut])
async def get_next_dialable_leads(
    campaign_id: uuid.UUID,
    limit: int = 10,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Fetch the next queue of dialable leads inside an active campaign."""
    campaign_service = CampaignService(db)
    return await campaign_service.select_next_leads(campaign_id, limit)
