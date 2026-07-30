import pytest
import uuid
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.campaign import Campaign
from app.models.campaign_lead import CampaignLead
from app.models.customer import Customer
from app.models.user import User
from app.repositories.base import BaseRepository

test_user_id = "22222222-2222-2222-2222-222222222222"
mock_admin_user = User(id=test_user_id, email="admin@example.com", role="admin", is_active=True)

class MockDbSession:
    def __init__(self, campaigns=None, leads=None, customers=None):
        self.campaigns = campaigns or {}
        self.leads = leads or {}
        self.customers = customers or {}

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

        stmt_str = str(statement)
        if "campaigns" in stmt_str:
            return Result(list(self.campaigns.values()))
        elif "campaign_leads" in stmt_str:
            return Result(list(self.leads.values()))
        elif "customers" in stmt_str:
            return Result(list(self.customers.values()))
        return Result([])

    async def get(self, model, id):
        name = str(model.__name__)
        if name == "Campaign":
            return self.campaigns.get(str(id))
        elif name == "CampaignLead":
            return self.leads.get(str(id))
        elif name == "Customer":
            return self.customers.get(str(id))
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
        
    monkeypatch.setattr(BaseRepository, "create", mock_base_create)

@pytest.mark.anyio
async def test_create_campaign_success(client: AsyncClient, override_auth, mock_repos):
    db_session = MockDbSession()
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db
    
    response = await client.post(
        "/api/v1/campaigns/",
        json={
            "name": "Hospital Outbound Call Campaign",
            "workflow_type": "hospital",
            "status": "draft",
            "max_retries": 3,
            "retry_interval_minutes": 30
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Hospital Outbound Call Campaign"
    assert data["workflow_type"] == "hospital"
    assert data["status"] == "draft"
    assert data["max_retries"] == 3

@pytest.mark.anyio
async def test_assign_leads_success(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    campaign_id = uuid.uuid4()
    cust_id = uuid.uuid4()
    
    campaign = Campaign(id=campaign_id, name="Real Estate Campaign", workflow_type="real_estate", max_retries=3)
    customer = Customer(id=cust_id, first_name="Tom", phone_number="+1000000000")
    
    db_session = MockDbSession(
        campaigns={str(campaign_id): campaign},
        customers={str(cust_id): customer}
    )
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db

    from app.repositories.campaign_lead import CampaignLeadRepository
    async def mock_lookup(self, camp_id, customer_id):
        return None
    monkeypatch.setattr(CampaignLeadRepository, "get_by_campaign_and_customer", mock_lookup)
    
    response = await client.post(
        f"/api/v1/campaigns/{campaign_id}/assign",
        json={"customer_ids": [str(cust_id)]}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["successfully_assigned"] == 1
    assert data["skipped_or_invalid"] == 0

@pytest.mark.anyio
async def test_retry_logic_service(mock_repos, monkeypatch):
    db_session = MockDbSession()
    from app.services.campaign_service import CampaignService
    service = CampaignService(db_session)
    
    campaign_id = uuid.uuid4()
    cust_id = uuid.uuid4()
    
    campaign = Campaign(id=campaign_id, name="Retry Campaign", workflow_type="hospital", max_retries=2, retry_interval_minutes=45)
    lead = CampaignLead(campaign_id=campaign_id, customer_id=cust_id, status="pending", retry_count=0)
    
    async def mock_get_campaign(self, id):
        return campaign
    async def mock_get_lead(self, camp_id, customer_id):
        return lead
    async def mock_update(self, obj, data):
        for k, v in data.items():
            setattr(obj, k, v)
        return obj

    from app.repositories.campaign import CampaignRepository
    from app.repositories.campaign_lead import CampaignLeadRepository

    monkeypatch.setattr(CampaignRepository, "get", mock_get_campaign)
    monkeypatch.setattr(CampaignLeadRepository, "get_by_campaign_and_customer", mock_get_lead)
    monkeypatch.setattr(CampaignLeadRepository, "update", mock_update)
    
    # 1. First failure -> Schedules retry
    updated = await service.record_call_outcome(campaign_id, cust_id, "failed")
    assert updated.status == "retry_scheduled"
    assert updated.retry_count == 1
    assert updated.next_attempt_at is not None
    
    # 2. Second failure (next_retry_count = 2 >= max_retries = 2) -> Marks lead as failed
    updated_exhausted = await service.record_call_outcome(campaign_id, cust_id, "failed")
    assert updated_exhausted.status == "failed"
    assert updated_exhausted.retry_count == 2
    assert updated_exhausted.next_attempt_at is None
