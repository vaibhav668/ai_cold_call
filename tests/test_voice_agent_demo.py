import pytest
import uuid
import json
import base64
import time
from datetime import datetime, timezone
from httpx import AsyncClient
from fastapi.testclient import TestClient

from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.models.campaign import Campaign
from app.models.customer import Customer
from app.voice_demo.models.voice_profile import VoiceProfile
from app.voice_demo.controllers.voice_agent import _demo_sessions

test_user_id = "22222222-2222-2222-2222-222222222222"
mock_admin_user = User(id=test_user_id, email="admin@example.com", role="admin", is_active=True)

class MockDbSession:
    def __init__(self, voice_profiles=None, campaigns=None, customers=None):
        self.voice_profiles = voice_profiles or {}
        self.campaigns = campaigns or {}
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
        
        stmt_str = str(statement).lower()
        if "voice_profiles" in stmt_str:
            return Result(list(self.voice_profiles.values()))
        elif "campaigns" in stmt_str:
            return Result(list(self.campaigns.values()))
        elif "customers" in stmt_str:
            return Result(list(self.customers.values()))
        return Result([])

    async def get(self, model, id):
        name = str(model.__name__)
        if name == "VoiceProfile":
            return self.voice_profiles.get(str(id))
        elif name == "Campaign":
            return self.campaigns.get(str(id))
        elif name == "Customer":
            return self.customers.get(str(id))
        return None

    def add(self, obj):
        if isinstance(obj, Customer):
            self.customers[str(obj.id)] = obj
        elif isinstance(obj, VoiceProfile):
            self.voice_profiles[str(obj.id)] = obj

    async def flush(self):
        pass

    async def commit(self):
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
async def test_get_voices_success(client: AsyncClient, override_auth):
    voice_id = str(uuid.uuid4())
    mock_voice = VoiceProfile(
        id=uuid.UUID(voice_id),
        name="Sophia",
        description="Professional Female",
        avatar="/avatars/sophia.png",
        gender="Female",
        supported_languages="English,Hindi,Telugu",
        voice_provider="melotts",
        voice_configuration='{"speaker_id": "EN_INDIA"}',
        status="active"
    )
    
    db_session = MockDbSession(voice_profiles={voice_id: mock_voice})
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db

    response = await client.get("/api/v1/voice-demo/voices")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Sophia"
    assert data[0]["gender"] == "Female"

@pytest.mark.anyio
async def test_get_industries_success(client: AsyncClient, override_auth):
    response = await client.get("/api/v1/voice-demo/industries")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert any(ind["id"] == "hospital" for ind in data)
    assert any(ind["id"] == "real_estate" for ind in data)

@pytest.mark.anyio
async def test_create_session_success_with_adaptation(client: AsyncClient, override_auth):
    # Setup voices: Sophia (supports Hindi/English), David (supports English only)
    sophia_id = str(uuid.uuid4())
    david_id = str(uuid.uuid4())
    
    sophia = VoiceProfile(
        id=uuid.UUID(sophia_id),
        name="Sophia",
        description="Professional Female",
        gender="Female",
        supported_languages="English,Hindi",
        voice_provider="melotts",
        voice_configuration='{"speaker_id": "EN_INDIA"}',
        status="active"
    )
    
    david = VoiceProfile(
        id=uuid.UUID(david_id),
        name="David",
        description="Sales Consultant Male",
        gender="Male",
        supported_languages="English",
        voice_provider="melotts",
        voice_configuration='{"speaker_id": "EN_US"}',
        status="active"
    )
    
    camp_id = str(uuid.uuid4())
    mock_campaign = Campaign(
        id=uuid.UUID(camp_id),
        name="Hospital Campaign",
        workflow_type="hospital",
        is_active=True
    )
    
    db_session = MockDbSession(
        voice_profiles={sophia_id: sophia, david_id: david},
        campaigns={camp_id: mock_campaign}
    )
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db

    # Request David (English only) with Hindi language -> should adapt to Sophia
    payload = {
        "voice_profile_id": david_id,
        "industry": "hospital",
        "language": "Hindi"
    }
    
    response = await client.post("/api/v1/voice-demo/sessions", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] is not None
    # Switched to Sophia because David doesn't support Hindi
    assert data["voice_profile"]["name"] == "Sophia"

@pytest.mark.anyio
async def test_websocket_stream_integration(override_auth, monkeypatch):
    client = TestClient(app)
    
    session_id = str(uuid.uuid4())
    camp_id = str(uuid.uuid4())
    cust_id = str(uuid.uuid4())
    
    # Pre-seed dynamic in-memory session metadata
    _demo_sessions[session_id] = {
        "session_id": session_id,
        "campaign_id": camp_id,
        "customer_id": cust_id,
        "voice_profile": None,
        "language": "English",
        "industry": "hospital",
        "created_at": datetime.now(),
        "start_time": None,
        "end_time": None,
        "transcript": []
    }

    # Mock DB Session
    mock_camp = Campaign(id=uuid.UUID(camp_id), name="Hospital Reminder", workflow_type="hospital")
    mock_cust = Customer(id=uuid.UUID(cust_id), first_name="Demo", last_name="User", phone_number="+1000")
    
    db_session = MockDbSession(
        campaigns={camp_id: mock_camp},
        customers={cust_id: mock_cust}
    )
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db

    # Mock Speech/STT
    from app.services.speech.stt.faster_whisper_provider import FasterWhisperProvider
    async def mock_transcribe(self, audio_bytes, language=None):
        return "hello reschedule please"
    monkeypatch.setattr(FasterWhisperProvider, "transcribe_utterance", mock_transcribe)

    with client.websocket_connect(f"/api/v1/voice-demo/stream/{session_id}") as ws:
        # Send raw 320 bytes Float32 downsampled PCM dummy frame (all zeros)
        ws.send_bytes(b"\x00" * 320)
        
        # We should receive the generated TTS play audio frame or state changes
        reply = ws.receive_text()
        data = json.loads(reply)
        assert "event" in data
        assert data["event"] in ["state_change", "transcript", "clear_audio"]

@pytest.mark.anyio
async def test_get_session_summary_success(client: AsyncClient, override_auth):
    session_id = str(uuid.uuid4())
    camp_id = str(uuid.uuid4())
    cust_id = str(uuid.uuid4())
    
    mock_voice = VoiceProfile(
        id=uuid.uuid4(),
        name="Sophia",
        description="Professional Female",
        gender="Female",
        supported_languages="English",
        voice_provider="melotts",
        voice_configuration='{}',
        status="active"
    )

    _demo_sessions[session_id] = {
        "session_id": session_id,
        "campaign_id": camp_id,
        "customer_id": cust_id,
        "voice_profile": mock_voice,
        "language": "English",
        "industry": "hospital",
        "created_at": datetime.now(),
        "start_time": time.time() - 30,
        "end_time": time.time(),
        "transcript": [
            {"sender": "agent", "text": "Hello Vaibhav.", "timestamp": datetime.utcnow().isoformat()},
            {"sender": "user", "text": "Yes hello Sophia.", "timestamp": datetime.utcnow().isoformat()}
        ]
    }

    # Test GET summary
    res_get = await client.get(f"/api/v1/voice-demo/summary/{session_id}")
    assert res_get.status_code == 200
    data_get = res_get.json()
    assert "summary" in data_get
    assert data_get["language"] == "English"
    assert data_get["voice_used"] == "Sophia"
    assert data_get["industry"] == "hospital"
    assert "lead_score" in data_get
    assert "site_visit_status" in data_get
    assert "extracted_variables" in data_get

    # Test POST summary
    res_post = await client.post(f"/api/v1/voice-demo/summary/{session_id}")
    assert res_post.status_code == 200
    data_post = res_post.json()
    assert data_post["language"] == "English"
    assert data_post["voice_used"] == "Sophia"
    assert data_post["industry"] == "hospital"

