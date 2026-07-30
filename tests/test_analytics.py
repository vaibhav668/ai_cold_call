import pytest
import uuid
from httpx import AsyncClient
from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.campaign import Campaign
from app.models.call_log import CallLog
from app.models.user import User

test_user_id = "22222222-2222-2222-2222-222222222222"
mock_admin_user = User(id=test_user_id, email="admin@example.com", role="admin", is_active=True)

class MockDbSession:
    def __init__(self, campaigns=None, call_logs=None):
        self.campaigns = campaigns or {}
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
            def all(self):
                return []
        return Result(list(self.call_logs.values()))

    async def get(self, model, id):
        name = str(model.__name__)
        if name == "Campaign":
            return self.campaigns.get(str(id))
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

@pytest.mark.anyio
async def test_analytics_summary_success(client: AsyncClient, override_auth):
    db_session = MockDbSession()
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db
    
    response = await client.get("/api/v1/analytics/summary")
    assert response.status_code == 200
    data = response.json()
    assert "total_campaigns" in data
    assert "total_calls" in data
    assert "success_rate_percentage" in data

@pytest.mark.anyio
async def test_campaign_analytics_success(client: AsyncClient, override_auth):
    campaign_id = uuid.uuid4()
    campaign = Campaign(id=campaign_id, name="Test Campaign", workflow_type="hospital")
    
    db_session = MockDbSession(
        campaigns={str(campaign_id): campaign}
    )
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db
    
    response = await client.get(f"/api/v1/analytics/campaigns/{campaign_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["campaign_name"] == "Test Campaign"
    assert "success_rate_percentage" in data
