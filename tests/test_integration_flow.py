import pytest
import uuid
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.db.session import get_db_session
from app.api.deps import get_current_user
from app.core.config import settings
from app.models.campaign import Campaign
from app.models.customer import Customer
from app.models.prompt_template import PromptTemplate
from app.models.call_log import CallLog
from app.models.document import Document
from app.models.user import User

class MockResult:
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

class MockDbSessionForIntegration:
    def __init__(self):
        self.campaigns = {}
        self.customers = {}
        self.documents = {}
        self.call_logs = {}

    async def execute(self, statement):
        stmt_str = str(statement).lower()
        
        # Dial context query or log queries
        if "call_logs" in stmt_str:
            log_list = list(self.call_logs.values())
            if not log_list:
                # Default mock log to satisfy resolve context
                mock_call = CallLog(
                    id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
                    campaign_id=uuid.UUID("8b68301f-3ac4-4043-9f15-c56d98df904e"),
                    customer_id=uuid.UUID("780a3d85-c788-46d6-a370-6602a6771bc6"),
                    plivo_call_uuid="test_integration_call_123",
                    status="ringing",
                    duration_seconds=10,
                    transcript=[{"sender": "customer", "text": "hello reschedule please"}],
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                return MockResult([mock_call])
            return MockResult(log_list)
            
        elif "prompt_templates" in stmt_str:
            mock_temp = PromptTemplate(
                id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
                campaign_id=uuid.UUID("8b68301f-3ac4-4043-9f15-c56d98df904e"),
                name="Mock Template",
                system_prompt="You are Sarah from Mercy Hospital. Confirm appointment with customer.",
                is_active=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            return MockResult([mock_temp])
            
        elif "customers" in stmt_str:
            cust_list = list(self.customers.values())
            if not cust_list:
                mock_cust = Customer(
                    id=uuid.UUID("780a3d85-c788-46d6-a370-6602a6771bc6"),
                    first_name="Rahul",
                    last_name="Sharma",
                    phone_number="+919876543210",
                    is_active=True,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                return MockResult([mock_cust])
            return MockResult(cust_list)
            
        elif "campaigns" in stmt_str:
            camp_list = list(self.campaigns.values())
            return MockResult(camp_list)

        return MockResult([])

    async def get(self, model, id_val):
        name = str(model.__name__)
        if name == "Campaign":
            return self.campaigns.get(str(id_val))
        elif name == "Customer":
            return self.customers.get(str(id_val))
        return None

    def add(self, obj):
        # Automatically generate default db attributes for model validation
        if not hasattr(obj, "id") or obj.id is None:
            obj.id = uuid.uuid4()
        if not hasattr(obj, "created_at") or obj.created_at is None:
            obj.created_at = datetime.now(timezone.utc)
        if not hasattr(obj, "updated_at") or obj.updated_at is None:
            obj.updated_at = datetime.now(timezone.utc)

        if isinstance(obj, Campaign):
            self.campaigns[str(obj.id)] = obj
        elif isinstance(obj, Customer):
            self.customers[str(obj.id)] = obj
        elif isinstance(obj, CallLog):
            self.call_logs[str(obj.id)] = obj
        elif isinstance(obj, Document):
            self.documents[str(obj.id)] = obj

    async def flush(self):
        pass
    async def commit(self):
        pass
    async def rollback(self):
        pass
    async def close(self):
        pass

@pytest.fixture(autouse=True)
def mock_plivo_credentials(monkeypatch):
    """Force mock Plivo credentials to avoid hitting real Plivo servers."""
    monkeypatch.setattr(settings, "PLIVO_AUTH_ID", "test_auth_id")
    monkeypatch.setattr(settings, "PLIVO_AUTH_TOKEN", "test_auth_token")

@pytest.fixture
def integration_db():
    db = MockDbSessionForIntegration()
    
    async def mock_db():
        yield db

    async def mock_user():
        return User(
            id=uuid.uuid4(),
            email="admin@example.com",
            role="admin",
            is_active=True
        )

    # Override database session and auth for integration tests
    app.dependency_overrides[get_db_session] = mock_db
    app.dependency_overrides[get_current_user] = mock_user
    
    yield db
    
    # Reset
    app.dependency_overrides.pop(get_db_session, None)
    app.dependency_overrides.pop(get_current_user, None)

def test_campaign_and_customer_creation(integration_db):
    client = TestClient(app)
    
    # 1. Create Campaign
    camp_payload = {
        "name": "Integration Test Hospital Campaign",
        "description": "Qualify patient appointments",
        "workflow_type": "hospital",
        "max_retries": 3,
        "retry_interval_minutes": 60,
        "is_active": True,
        "status": "active"
    }
    response = client.post("/api/v1/campaigns/", json=camp_payload)
    assert response.status_code == 201
    camp_data = response.json()
    assert camp_data["name"] == camp_payload["name"]
    assert "id" in camp_data
    
    # Verify campaign is in mock db
    camp_id = camp_data["id"]
    assert camp_id in integration_db.campaigns

    # 2. Get Campaigns list (verify array parsing schema)
    list_resp = client.get("/api/v1/campaigns/")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert "items" in list_data
    assert len(list_data["items"]) >= 1

def test_document_indexing_rag(integration_db):
    client = TestClient(app)
    camp_id = str(uuid.uuid4())
    integration_db.campaigns[camp_id] = Campaign(
        id=uuid.UUID(camp_id),
        name="Mock Hospital",
        workflow_type="hospital"
    )
    
    # Mock RAGService.index_document to skip vector upload
    from app.services.rag_service import RAGService
    with patch.object(RAGService, "index_document", return_value=3) as mock_index:
        # Upload mock txt document
        file_content = b"Mercy Hospital Parking is free in the East Wing lot."
        files = {"file": ("hospital_faq.txt", file_content, "text/plain")}
        
        response = client.post(
            f"/api/v1/campaigns/{camp_id}/documents",
            files=files
        )
        assert response.status_code == 201
        doc_data = response.json()
        assert doc_data["filename"] == "hospital_faq.txt"
        assert "id" in doc_data
        
        # Verify indexed document count in DB
        assert doc_data["id"] in integration_db.documents
        mock_index.assert_called_once()

def test_outbound_call_trigger(integration_db):
    client = TestClient(app)
    
    camp_id = str(uuid.uuid4())
    cust_id = str(uuid.uuid4())
    
    integration_db.campaigns[camp_id] = Campaign(id=uuid.UUID(camp_id), name="Reminder", workflow_type="hospital")
    integration_db.customers[cust_id] = Customer(id=uuid.UUID(cust_id), first_name="Rahul", phone_number="+919876543210")
    
    # Trigger dial
    response = client.post(
        "/api/v1/telephony/dial",
        json={
            "campaign_id": camp_id,
            "customer_id": cust_id,
            "phone_number": "+919876543210"
        }
    )
    assert response.status_code == 200
    dial_data = response.json()
    assert dial_data["status"] == "queued"
    assert "request_uuid" in dial_data

def test_websocket_conversation_flow(integration_db, monkeypatch):
    client = TestClient(app)
    call_id = "test_integration_call_123"
    
    # 1. Mock STT to return "yes, confirm my appointment please" to test confirmation tool
    from app.services.speech.stt.faster_whisper_provider import FasterWhisperProvider
    async def mock_stt_transcribe(self, audio_bytes, language=None):
        return "yes, confirm my appointment please"
    monkeypatch.setattr(FasterWhisperProvider, "transcribe_utterance", mock_stt_transcribe)

    # Mock LLMManager to avoid real outbound API calls
    from app.services.llm_service import LLMManager
    async def mock_generate_stream(self, messages, tools=None):
        yield "Your appointment is confirmed. Thank you. Goodbye!", None
    monkeypatch.setattr(LLMManager, "generate_completion_stream", mock_generate_stream)

    with client.websocket_connect(f"/api/v1/telephony/stream/{call_id}") as ws:
        # Start connection
        ws.send_text(json.dumps({
            "event": "start",
            "start": {
                "callSid": call_id,
                "streamSid": "stream_1"
            }
        }))

        # Send media chunk. VAD triggers end of speech -> transcribes "confirm my appointment" -> calls confirm_appointment tool
        ws.send_text(json.dumps({
            "event": "media",
            "media": {
                "track": "inbound",
                "payload": "c2lsZW5jZQ=="
            }
        }))
        
        # Read the mock audio frames sent back by bot
        reply = ws.receive_text()
        data = json.loads(reply)
        assert data["event"] == "playAudio"
        assert "payload" in data["media"]

def test_websocket_reschedule_conversation_flow(integration_db, monkeypatch):
    client = TestClient(app)
    call_id = "test_integration_call_123"
    
    # 2. Mock STT to return "I want to reschedule for Monday" to test reschedule tool
    from app.services.speech.stt.faster_whisper_provider import FasterWhisperProvider
    async def mock_stt_transcribe(self, audio_bytes, language=None):
        return "I want to reschedule for next Monday at 2 pm"
    monkeypatch.setattr(FasterWhisperProvider, "transcribe_utterance", mock_stt_transcribe)

    # Mock LLMManager to avoid real outbound API calls
    from app.services.llm_service import LLMManager
    async def mock_generate_stream(self, messages, tools=None):
        yield "No problem. I have rescheduled your appointment.", None
    monkeypatch.setattr(LLMManager, "generate_completion_stream", mock_generate_stream)

    with client.websocket_connect(f"/api/v1/telephony/stream/{call_id}") as ws:
        # Start connection
        ws.send_text(json.dumps({
            "event": "start",
            "start": {
                "callSid": call_id,
                "streamSid": "stream_1"
            }
        }))

        # Send media chunk
        ws.send_text(json.dumps({
            "event": "media",
            "media": {
                "track": "inbound",
                "payload": "c2lsZW5jZQ=="
            }
        }))
        
        reply = ws.receive_text()
        data = json.loads(reply)
        assert data["event"] == "playAudio"
