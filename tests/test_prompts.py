import pytest
import uuid
from datetime import datetime, timezone
from httpx import AsyncClient
from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.campaign import Campaign
from app.models.customer import Customer
from app.models.prompt_template import PromptTemplate
from app.models.user import User
from app.repositories.base import BaseRepository

test_user_id = "22222222-2222-2222-2222-222222222222"
mock_admin_user = User(id=test_user_id, email="admin@example.com", role="admin", is_active=True)

class MockDbSession:
    def __init__(self, campaigns=None, customers=None, templates=None):
        self.campaigns = campaigns or {}
        self.customers = customers or {}
        self.templates = templates or {}

    async def execute(self, statement):
        class Result:
            def __init__(self, data):
                self._data = data
            def scalars(self):
                class Scalars:
                    def __init__(self, d):
                        self._d = d
                    def first(self):
                        return self._d[0] if self._d else None
                    def all(self):
                        return self._d
                return Scalars(self._data)
            def scalar_one(self):
                return len(self._data)
        return Result(list(self.templates.values()))

    async def get(self, model, id):
        name = str(model.__name__)
        if name == "Campaign":
            return self.campaigns.get(str(id))
        elif name == "Customer":
            return self.customers.get(str(id))
        elif name == "PromptTemplate":
            return self.templates.get(str(id))
        return None

    def add(self, obj):
        pass
    async def flush(self):
        pass
    async def commit(self):
        pass
    async def rollback(self):
        pass
    async def close(self):
        pass

@pytest.fixture
def override_auth():
    async def mock_current_user():
        return mock_admin_user
    app.dependency_overrides[get_current_user] = mock_current_user
    yield
    app.dependency_overrides.clear()

@pytest.fixture
def mock_repos(monkeypatch):
    async def mock_base_create(self, obj):
        if isinstance(obj, dict):
            obj_inst = self.model(**obj)
        else:
            obj_inst = obj
        if getattr(obj_inst, "id", None) is None:
            obj_inst.id = uuid.uuid4()
        if getattr(obj_inst, "created_at", None) is None:
            obj_inst.created_at = datetime.now(timezone.utc)
        if getattr(obj_inst, "updated_at", None) is None:
            obj_inst.updated_at = datetime.now(timezone.utc)
        return obj_inst
        
    async def mock_base_update(self, obj, data):
        for k, v in data.items():
            setattr(obj, k, v)
        return obj

    monkeypatch.setattr(BaseRepository, "create", mock_base_create)
    monkeypatch.setattr(BaseRepository, "update", mock_base_update)

@pytest.mark.anyio
async def test_create_prompt_template_success(client: AsyncClient, override_auth, mock_repos):
    campaign_id = uuid.uuid4()
    campaign = Campaign(id=campaign_id, name="Test Campaign", workflow_type="hospital")
    
    db_session = MockDbSession(campaigns={str(campaign_id): campaign})
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db
    
    response = await client.post(
        f"/api/v1/campaigns/{campaign_id}/prompts",
        json={
            "name": "Mercy Hospital Greeting",
            "system_prompt": "You are a receptionist welcoming {{first_name}}.",
            "language_prompt": "Speak calmly.",
            "conversation_goals": "Confirm appointment time.",
            "is_active": True
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Mercy Hospital Greeting"
    assert "receptionist" in data["system_prompt"]
    assert data["is_active"] is True

@pytest.mark.anyio
async def test_compile_prompt_success(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    campaign_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    
    campaign = Campaign(id=campaign_id, name="Test Campaign", workflow_type="hospital")
    customer = Customer(
        id=customer_id,
        first_name="Alice",
        phone_number="+1000000000",
        custom_variables={"appointment_time": "10:30 AM"}
    )
    template = PromptTemplate(
        id=uuid.uuid4(),
        campaign_id=campaign_id,
        name="Mercy Hospital",
        system_prompt="Receptionist welcoming {{first_name}} at {{appointment_time}}.",
        language_prompt="Speak calmly to {{first_name}}.",
        conversation_goals="Confirm {{appointment_time}}.",
        is_active=True
    )
    
    db_session = MockDbSession(
        campaigns={str(campaign_id): campaign},
        customers={str(customer_id): customer},
        templates={str(template.id): template}
    )
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db

    from app.repositories.prompt_template import PromptTemplateRepository
    async def mock_active(self, camp_id):
        return template
    monkeypatch.setattr(PromptTemplateRepository, "get_active_by_campaign", mock_active)
    
    from app.services.rag_service import RAGService
    async def mock_rag(self, campaign_id, query, limit=5):
        return [{"text": "Fact: Clinic address is 100 Main St."}]
    monkeypatch.setattr(RAGService, "search_knowledge", mock_rag)
    
    response = await client.get(
        f"/api/v1/campaigns/{campaign_id}/prompts/compile",
        params={"customer_id": str(customer_id), "rag_query": "clinic location"}
    )
    assert response.status_code == 200
    data = response.json()
    
    compiled = data["compiled_prompt"]
    assert "Receptionist welcoming Alice at 10:30 AM." in compiled
    assert "Speak calmly to Alice." in compiled
    assert "Confirm 10:30 AM." in compiled
    assert "Fact: Clinic address is 100 Main St." in compiled
