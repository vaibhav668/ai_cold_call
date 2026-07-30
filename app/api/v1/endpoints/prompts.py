from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import uuid
from app.db.session import get_db_session
from app.api.deps import get_current_user, RoleChecker
from app.schemas.prompt_template import PromptTemplateOut, PromptTemplateCreate, PromptTemplateUpdate, PromptTemplateCompileOut
from app.repositories.prompt_template import PromptTemplateRepository
from app.services.prompt_service import PromptService
from app.models.user import User

router = APIRouter()

async def _deactivate_other_templates(
    db: AsyncSession,
    campaign_id: uuid.UUID,
    active_template_id: uuid.UUID
) -> None:
    """Helper method to deactivate other templates for this campaign to ensure single active constraint."""
    template_repo = PromptTemplateRepository(db)
    templates = await template_repo.get_by_campaign(campaign_id)
    for t in templates:
        if t.id != active_template_id and t.is_active:
            await template_repo.update(t, {"is_active": False})

@router.get("/campaigns/{campaign_id}/prompts", response_model=List[PromptTemplateOut])
async def list_campaign_templates(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Retrieve list of prompt configurations for a campaign."""
    template_repo = PromptTemplateRepository(db)
    return await template_repo.get_by_campaign(campaign_id)

@router.post("/campaigns/{campaign_id}/prompts", response_model=PromptTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_prompt_template(
    campaign_id: uuid.UUID,
    template_in: PromptTemplateCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Create a new prompt template for a campaign."""
    template_repo = PromptTemplateRepository(db)
    
    template_data = template_in.model_dump()
    template_data["campaign_id"] = campaign_id
    
    db_obj = await template_repo.create(template_data)
    await db.commit()
    
    if db_obj.is_active:
        await _deactivate_other_templates(db, campaign_id, db_obj.id)
        await db.commit()
        
    return db_obj

@router.put("/prompts/{template_id}", response_model=PromptTemplateOut)
async def update_prompt_template(
    template_id: uuid.UUID,
    template_in: PromptTemplateUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Update configurations of an existing prompt template."""
    template_repo = PromptTemplateRepository(db)
    template = await template_repo.get(template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prompt template not found."
        )
        
    update_data = template_in.model_dump(exclude_unset=True)
    updated_obj = await template_repo.update(template, update_data)
    await db.commit()
    
    if updated_obj.is_active:
        await _deactivate_other_templates(db, updated_obj.campaign_id, updated_obj.id)
        await db.commit()
        
    return updated_obj

@router.delete("/prompts/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(RoleChecker(["admin", "manager"]))
):
    """Delete a prompt template configuration from the database."""
    template_repo = PromptTemplateRepository(db)
    template = await template_repo.get(template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prompt template not found."
        )
    await template_repo.remove(template_id)
    await db.commit()

@router.get("/campaigns/{campaign_id}/prompts/compile", response_model=PromptTemplateCompileOut)
async def compile_prompt(
    campaign_id: uuid.UUID,
    customer_id: uuid.UUID,
    rag_query: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Review and compile dynamic prompt text resolving placeholders and optional semantic search context."""
    prompt_service = PromptService(db)
    compiled_prompt, resolved_vars = await prompt_service.build_prompt(
        campaign_id=campaign_id,
        customer_id=customer_id,
        rag_query=rag_query
    )
    return {
        "campaign_id": campaign_id,
        "customer_id": customer_id,
        "resolved_variables": resolved_vars,
        "compiled_prompt": compiled_prompt
    }
