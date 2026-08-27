import pytest
import json
import base64
import uuid
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.campaign import Campaign
from app.models.customer import Customer
from app.models.prompt_template import PromptTemplate
from app.models.user import User
from app.services.vad_service import VADService

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
                plivo_call_uuid="test_plivo_call_uuid_vad",
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

@pytest.mark.anyio
async def test_vad_speech_thresholds():
    vad = VADService(threshold=1000.0)
    
    # 1. Silent PCMU frame (0xFF bytes) -> RMS should be very low
    silent_frame = b"\xFF" * 160
    assert vad.is_speech(silent_frame) is False
    
    # 2. High amplitude G.711 PCMU frame (0x00 bytes) -> RMS should be high
    loud_frame = b"\x00" * 160
    assert vad.is_speech(loud_frame) is True

def test_streaming_interruption_via_websocket(override_auth, monkeypatch):
    client = TestClient(app)
    
    campaign_id = uuid.uuid4()
    customer_id = uuid.uuid4()
    call_id = "test_plivo_call_uuid_vad"
    
    campaign = Campaign(id=campaign_id, name="Outbound", workflow_type="hospital")
    customer = Customer(id=customer_id, first_name="Alice", phone_number="+1000000000")
    template = PromptTemplate(id=uuid.uuid4(), campaign_id=campaign_id, name="Template", system_prompt="Greeting", is_active=True)
    
    db_session = MockDbSession(
        campaigns={str(campaign_id): campaign},
        customers={str(customer_id): customer},
        templates={str(template.id): template}
    )
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db

    # Mock dynamic STT chunking response to trigger transcription turn
    from app.services.speech.stt.faster_whisper_provider import FasterWhisperProvider
    async def mock_transcribe(self, audio_bytes, language=None):
        return "hello reschedule please"
    monkeypatch.setattr(FasterWhisperProvider, "transcribe_utterance", mock_transcribe)

    from app.services.llm_service import LLMManager
    async def mock_generate_stream(self, messages, tools=None):
        yield "Hello! I can help you with your appointment.", None
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
        
        # Send media chunk. It will trigger STT mock transcript -> process turn -> stream mock TTS audio frames
        ws.send_text(json.dumps({
            "event": "media",
            "media": {
                "payload": "c2lsZW5jZQ==" # 'silence' base64
            }
        }))
        
        # Read the media frames sent back by bot
        replies = []
        for _ in range(5):
            reply = ws.receive_text()
            data = json.loads(reply)
            replies.append(data)
            
        assert len(replies) == 5
        assert all(r["event"] == "playAudio" for r in replies)
