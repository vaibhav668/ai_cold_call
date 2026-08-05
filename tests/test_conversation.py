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
    def __init__(self, campaigns=None, customers=None, templates=None, call_logs=None):
        self.campaigns = campaigns or {}
        self.customers = customers or {}
        self.templates = templates or {}
        self.call_logs = call_logs or {}

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
        return Result([])

    async def get(self, model, id):
        name = str(model.__name__)
        if name == "Campaign":
            return self.campaigns.get(str(id))
        elif name == "Customer":
            return self.customers.get(str(id))
        elif name == "PromptTemplate":
            return self.templates.get(str(id))
        elif name == "CallLog":
            return self.call_logs.get(str(id))
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
async def test_conversation_turn_mock_llm(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    campaign_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    call_id = "test_plivo_call_uuid_abc"
    
    campaign = Campaign(id=campaign_id, name="Outbound", workflow_type="hospital")
    customer = Customer(id=customer_id, first_name="Alice", phone_number="+1000000000")
    template = PromptTemplate(
        id=uuid.uuid4(),
        campaign_id=campaign_id,
        name="Template",
        system_prompt="Greeting system.",
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

    from app.services.prompt_service import PromptService
    async def mock_build(self, campaign_id, customer_id, rag_query=None, **kwargs):
        return "Welcoming Alice.", {}
    monkeypatch.setattr(PromptService, "build_prompt", mock_build)
    
    # Mock LLMManager to avoid outbound Groq API requests during test execution
    from app.services.llm_service import LLMManager
    async def mock_generate(self, messages, tools=None):
        return "Hello! I can help you book an appointment.", None
    monkeypatch.setattr(LLMManager, "generate_completion", mock_generate)
    
    response = await client.post(
        f"/api/v1/conversation/{call_id}/turn",
        json={
            "campaign_id": str(campaign_id),
            "customer_id": str(customer_id),
            "user_text": "Hello, who is this?"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "response_text" in data
    assert data["should_hangup"] is False
    assert data["should_transfer"] is False

@pytest.mark.anyio
async def test_conversation_turn_book_appointment(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    campaign_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    call_id = "test_plivo_call_uuid_book"
    
    campaign = Campaign(id=campaign_id, name="Outbound", workflow_type="hospital")
    customer = Customer(id=customer_id, first_name="Alice", phone_number="+1000000000")
    template = PromptTemplate(id=uuid.uuid4(), campaign_id=campaign_id, name="Template", system_prompt="Rec", is_active=True)
    
    db_session = MockDbSession(
        campaigns={str(campaign_id): campaign},
        customers={str(customer_id): customer},
        templates={str(template.id): template}
    )
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db

    from app.services.prompt_service import PromptService
    async def mock_build(self, campaign_id, customer_id, rag_query=None, **kwargs):
        return "Receptionist", {}
    monkeypatch.setattr(PromptService, "build_prompt", mock_build)

    # Mock LLMManager to trigger the book_appointment tool call
    from app.services.llm_service import LLMManager
    async def mock_generate(self, messages, tools=None):
        return None, [
            {
                "id": "call_mock_book_123",
                "type": "function",
                "function": {
                    "name": "book_appointment",
                    "arguments": '{"date": "2026-08-01", "time": "14:00"}'
                }
            }
        ]
    monkeypatch.setattr(LLMManager, "generate_completion", mock_generate)

    response = await client.post(
        f"/api/v1/conversation/{call_id}/turn",
        json={
            "campaign_id": str(campaign_id),
            "customer_id": str(customer_id),
            "user_text": "I want to book an appointment please"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["should_hangup"] is False

@pytest.mark.anyio
async def test_end_conversation_saves_transcript(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    campaign_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    call_id = "test_plivo_call_uuid_end"
    
    db_session = MockDbSession()
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db
    
    from app.services.session_manager import SessionManager
    manager = SessionManager()
    await manager.append_message(call_id, {"role": "user", "content": "Hello"})
    await manager.append_message(call_id, {"role": "assistant", "content": "Hi there"})
    
    response = await client.post(
        f"/api/v1/conversation/{call_id}/end",
        json={
            "campaign_id": str(campaign_id),
            "customer_id": str(customer_id),
            "phone_number": "+1234567890",
            "duration_seconds": 15
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "session_terminated"
    assert "call_log_id" in data
    
    history = await manager.get_message_history(call_id)
    assert len(history) == 0
