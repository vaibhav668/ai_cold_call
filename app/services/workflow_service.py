import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.prompt_template import PromptTemplateRepository
from app.models.prompt_template import PromptTemplate
from app.core.logging import logger

class WorkflowService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.template_repo = PromptTemplateRepository(db)

    async def seed_campaign_defaults(self, campaign_id: uuid.UUID, workflow_type: str) -> None:
        """Seed and activate a default workflow-specific PromptTemplate configuration for a campaign."""
        if workflow_type == "hospital":
            template = PromptTemplate(
                campaign_id=campaign_id,
                name="Mercy Hospital Appointment Reminder & Feedback Script",
                system_prompt=(
                    "You are Sarah, a warm and helpful patient coordinator at Mercy Hospital. "
                    "Your task is to contact {{first_name}} {{last_name}} regarding their upcoming appointment "
                    "scheduled for {{appointment_date}} at {{appointment_time}} in our {{department}} department. "
                    "Always speak with compassion, clarify patient identity first, and address any FAQs they raise."
                ),
                language_prompt=(
                    "Speak clearly, use simple vocabulary, and short conversational sentences "
                    "suitable for high-quality text-to-speech rendering. Keep a professional, "
                    "reassuring, and gentle tone."
                ),
                conversation_goals=(
                    "- Goal 1: Confirm you are speaking with {{first_name}} {{last_name}}.\n"
                    "- Goal 2: Remind them of their appointment on {{appointment_date}} at {{appointment_time}} in the {{department}} department.\n"
                    "- Goal 3: Ask them to confirm if they are coming (Yes/No).\n"
                    "- Goal 4: If they cannot make it, offer to reschedule. If they confirm rescheduling, trigger the 'book_appointment' tool.\n"
                    "- Goal 5: Ask if they have any questions. If they ask about parking, location, or cancellation policies, trigger the 'lookup_knowledge' tool.\n"
                    "- Goal 6: Capture patient satisfaction feedback score (out of 5) and thank them."
                ),
                is_active=True
            )
            await self.template_repo.create(template)
            logger.info(f"Seeded default hospital prompt template for campaign: {campaign_id}")
            
        elif workflow_type == "real_estate":
            template = PromptTemplate(
                campaign_id=campaign_id,
                name="Premium Realty Lead Qualification Script",
                system_prompt=(
                    "You are James, a professional real estate coordinator at Premium Realty. "
                    "Your task is to contact {{first_name}} {{last_name}} to qualify them as a prospective buyer "
                    "and schedule a physical showing for our {{property_name}} listing. "
                    "Always speak with enthusiasm, gather buyer parameters, and highlight key listing advantages."
                ),
                language_prompt=(
                    "Speak with a confident, polite, energetic, and articulate tone. "
                    "Keep sentences engaging, conversational, and direct."
                ),
                conversation_goals=(
                    "- Goal 1: Verify you are speaking with {{first_name}}.\n"
                    "- Goal 2: Introduce the listing at {{property_name}} located in {{location}}.\n"
                    "- Goal 3: Qualify the buyer's budget threshold. Ask if their budget accommodates the listing price of {{price}}.\n"
                    "- Goal 4: Determine their desired time to buy (e.g. within 3 months, 6 months).\n"
                    "- Goal 5: Offer to book a physical site visit. If they agree to a tour, trigger the 'book_appointment' tool to schedule a showing date/time.\n"
                    "- Goal 6: If they ask questions about property taxes, amenities, or local schools, trigger the 'lookup_knowledge' tool."
                ),
                is_active=True
            )
            await self.template_repo.create(template)
            logger.info(f"Seeded default real estate prompt template for campaign: {campaign_id}")
            
        else:
            logger.warning(f"Unknown workflow type '{workflow_type}' requested for campaign seeding: {campaign_id}")
