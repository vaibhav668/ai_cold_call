import uuid
import re
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.prompt_template import PromptTemplateRepository
from app.repositories.customer import CustomerRepository
from app.services.rag_service import RAGService
from app.core.exceptions import NotFoundException

class PromptService: 
    def __init__(self, db: AsyncSession):
        self.db = db
        self.template_repo = PromptTemplateRepository(db)
        self.customer_repo = CustomerRepository(db)
        self.rag_service = RAGService()

    def _replace_placeholders(self, text: Optional[str], variables: Dict[str, Any]) -> str:
        """Replace all curly brace placeholders {{var}} with resolved values."""
        if not text:
            return ""
            
        def replacement(match):
            key = match.group(1).strip()
            return str(variables.get(key, ""))
            
        return re.sub(r"\{\{([^}]+)\}\}", replacement, text)

    async def build_prompt(
        self,
        campaign_id: uuid.UUID,
        customer_id: uuid.UUID,
        rag_query: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """Compile dynamic conversation prompt resolving template placeholders and appending RAG facts."""
        template = await self.template_repo.get_active_by_campaign(campaign_id)
        if not template:
            raise NotFoundException("Active prompt template not configured for this campaign.")
            
        customer = await self.customer_repo.get(customer_id)
        if not customer:
            raise NotFoundException("Customer not found.")
            
        variables = {
            "first_name": customer.first_name or "",
            "last_name": customer.last_name or "",
            "email": customer.email or "",
            "phone_number": customer.phone_number or "",
            "id": str(customer.id)
        }
        
        if customer.custom_variables and isinstance(customer.custom_variables, dict):
            for k, v in customer.custom_variables.items():
                variables[k] = v
                
        # Resolve dynamic identity variables
        agent_name = "Sophia"
        hospital_name = "CityCare Hospital"
        builder = "Skyline Developers"
        property_name = "3 BHK Apartment"

        if session_id:
            from app.services.session_manager import SessionManager
            sm = SessionManager()
            session_meta = await sm.get_session_metadata(session_id)
            if session_meta:
                agent_name = session_meta.get("agent_name", "Sophia")

        variables["agent_name"] = agent_name
        variables["hospital_name"] = hospital_name
        variables["builder"] = builder
        variables["property_name"] = property_name

        compiled_sys = self._replace_placeholders(template.system_prompt, variables)
        compiled_goals = self._replace_placeholders(template.conversation_goals, variables)
        compiled_lang = self._replace_placeholders(template.language_prompt, variables)
        
        # Replace hardcoded values to match runtime identity selections
        compiled_sys = compiled_sys.replace("Sarah", agent_name).replace("James", agent_name)
        compiled_sys = compiled_sys.replace("Mercy Hospital", hospital_name)
        compiled_sys = compiled_sys.replace("Premium Realty", builder)

        compiled_goals = compiled_goals.replace("Sarah", agent_name).replace("James", agent_name)
        compiled_goals = compiled_goals.replace("Mercy Hospital", hospital_name)
        compiled_goals = compiled_goals.replace("Premium Realty", builder)

        prompt_parts = [
            "### SYSTEM ROLE & INSTRUCTIONS",
            compiled_sys
        ]
        
        if compiled_goals:
            prompt_parts.extend([
                "",
                "### CONVERSATION GOALS",
                compiled_goals
            ])
            
        if compiled_lang:
            prompt_parts.extend([
                "",
                "### STYLE & LANGUAGE GUIDELINES",
                compiled_lang
            ])
            
        # Skip local SentenceTransformer model load on the CALL_START greeting initialization to prevent websocket timeouts
        if rag_query and rag_query != "[CALL_START]":
            facts = await self.rag_service.search_knowledge(campaign_id, rag_query, limit=3)
            if facts:
                facts_text = "\n".join([f"- {item['text']}" for item in facts])
                prompt_parts.extend([
                    "",
                    "### RETRIEVED KNOWLEDGE BASE FACTS",
                    facts_text
                ])
                
        final_prompt = "\n".join(prompt_parts)
        return final_prompt, variables
