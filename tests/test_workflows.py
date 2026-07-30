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
    def __init__(self, campaigns=None, templates=None):
        self.campaigns = campaigns or {}
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
        return obj_inst
        
    monkeypatch.setattr(BaseRepository, "create", mock_base_create)

@pytest.mark.anyio
async def test_hospital_campaign_seeding(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    db_session = MockDbSession()
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db
    
    seeded_templates = []
    from app.services.workflow_service import WorkflowService
    async def mock_seed(self, campaign_id, workflow_type):
        seeded_templates.append(workflow_type)
    monkeypatch.setattr(WorkflowService, "seed_campaign_defaults", mock_seed)
    
    response = await client.post(
        "/api/v1/campaigns/",
        json={
            "name": "Mercy Hospital Reminders",
            "workflow_type": "hospital",
            "status": "active"
        }
    )
    assert response.status_code == 201
    assert "hospital" in seeded_templates

@pytest.mark.anyio
async def test_real_estate_campaign_seeding(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    db_session = MockDbSession()
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db
    
    seeded_templates = []
    from app.services.workflow_service import WorkflowService
    async def mock_seed(self, campaign_id, workflow_type):
        seeded_templates.append(workflow_type)
    monkeypatch.setattr(WorkflowService, "seed_campaign_defaults", mock_seed)
    
    response = await client.post(
        "/api/v1/campaigns/",
        json={
            "name": "Premium Realty Showcase",
            "workflow_type": "real_estate",
            "status": "active"
        }
    )
    assert response.status_code == 201
    assert "real_estate" in seeded_templates

@pytest.mark.anyio
async def test_workflow_prompts_compile_resolution(monkeypatch):
    db_session = MockDbSession()
    from app.services.workflow_service import WorkflowService
    seeder = WorkflowService(db_session)
    
    campaign_id = uuid.uuid4()
    
    created_templates = []
    async def mock_create(obj):
        created_templates.append(obj)
        return obj
    monkeypatch.setattr(seeder.template_repo, "create", mock_create)
    
    await seeder.seed_campaign_defaults(campaign_id, "hospital")
    assert len(created_templates) == 1
    t = created_templates[0]
    assert "receptionist" in t.system_prompt.lower() or "coordinator" in t.system_prompt.lower()
    assert "{{appointment_date}}" in t.system_prompt
    assert "{{department}}" in t.system_prompt
