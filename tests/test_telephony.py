import pytest
import uuid
import json
from httpx import AsyncClient
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.campaign import Campaign
from app.models.customer import Customer
from app.models.user import User
from app.repositories.base import BaseRepository

test_user_id = "22222222-2222-2222-2222-222222222222"
mock_admin_user = User(id=test_user_id, email="admin@example.com", role="admin", is_active=True)

class MockDbSession:
    def __init__(self, campaigns=None, customers=None, call_logs=None, leads=None):
        self.campaigns = campaigns or {}
        self.customers = customers or {}
        self.call_logs = call_logs or {}
        self.leads = leads or {}

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

        import uuid
        from app.models.call_log import CallLog
        from app.models.prompt_template import PromptTemplate
        from app.models.customer import Customer

        stmt_str = str(statement).lower()
        if "call_logs" in stmt_str:
            mock_call = CallLog(
                id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
                campaign_id=uuid.UUID("8b68301f-3ac4-4043-9f15-c56d98df904e"),
                customer_id=uuid.UUID("780a3d85-c788-46d6-a370-6602a6771bc6"),
                plivo_call_uuid="test_call_sid_123",
                status="ringing"
            )
            return Result([mock_call])
        elif "prompt_templates" in stmt_str:
            mock_temp = PromptTemplate(
                id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
                campaign_id=uuid.UUID("8b68301f-3ac4-4043-9f15-c56d98df904e"),
                name="Mock Template",
                system_prompt="Greeting",
                is_active=True
            )
            return Result([mock_temp])
        elif "customers" in stmt_str:
            mock_cust = Customer(
                id=uuid.UUID("780a3d85-c788-46d6-a370-6602a6771bc6"),
                first_name="Akash",
                last_name="Sharma",
                phone_number="+1234567890",
                is_active=True
            )
            return Result([mock_cust])

        return Result([])

    async def get(self, model, id):
        name = str(model.__name__)
        if name == "Campaign":
            return self.campaigns.get(str(id))
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
        return obj_inst
        
    async def mock_base_update(self, obj, data):
        for k, v in data.items():
            setattr(obj, k, v)
        return obj

    monkeypatch.setattr(BaseRepository, "create", mock_base_create)
    monkeypatch.setattr(BaseRepository, "update", mock_base_update)

@pytest.mark.anyio
async def test_trigger_outbound_dial_success(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PLIVO_AUTH_ID", "test_auth_id")
    monkeypatch.setattr(settings, "PLIVO_AUTH_TOKEN", "test_auth_token")
    
    campaign_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    
    db_session = MockDbSession()
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db
    
    response = await client.post(
        "/api/v1/telephony/dial",
        json={
            "campaign_id": str(campaign_id),
            "customer_id": str(customer_id),
            "phone_number": "+1234567890"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "request_uuid" in data
    assert data["status"] == "queued"

@pytest.mark.anyio
async def test_xml_callbacks_webhooks(client: AsyncClient):
    campaign_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    
    # 1. Answer webhook XML response
    response_ans = await client.post(
        f"/api/v1/telephony/answer?campaign_id={campaign_id}&customer_id={customer_id}",
        data={"CallUUID": "test_uuid_xml"}
    )
    assert response_ans.status_code == 200
    assert "application/xml" in response_ans.headers["content-type"]
    assert "<Stream" in response_ans.text
    
    # 2. Inbound callback XML response
    response_inb = await client.post(
        "/api/v1/telephony/inbound",
        data={"CallUUID": "test_uuid_inb"}
    )
    assert response_inb.status_code == 200
    assert "<Speak>" in response_inb.text
    assert "<Stream" in response_inb.text

def test_websocket_audio_stream(monkeypatch):
    from app.services.speech.stt.faster_whisper_provider import FasterWhisperProvider
    async def mock_transcribe(self, audio_bytes, language=None):
        return "hello reschedule please"
    monkeypatch.setattr(FasterWhisperProvider, "transcribe_utterance", mock_transcribe)

    # We use FastAPI's sync TestClient for Websockets routing tests
    client = TestClient(app)
    with client.websocket_connect("/api/v1/telephony/stream/test_call_sid_123") as ws:
        # Send start event
        ws.send_text(json.dumps({
            "event": "start",
            "start": {
                "callSid": "test_call_sid_123",
                "streamSid": "stream_sid_abc"
            }
        }))
        
        # Send binary media payload (base64 encoded)
        ws.send_text(json.dumps({
            "event": "media",
            "media": {
                "track": "inbound",
                "payload": "c2lsZW5jZQ==" # 'silence' base64
            }
        }))
        
        # Expect simulated bot media response
        reply = ws.receive_text()
        data = json.loads(reply)
        assert data["event"] == "playAudio"
        assert "payload" in data["media"]
        
        # Send stop event
        ws.send_text(json.dumps({
            "event": "stop",
            "stop": {
                "callSid": "test_call_sid_123"
            }
        }))
