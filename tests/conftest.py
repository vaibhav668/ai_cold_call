import pytest
import uuid
from unittest.mock import MagicMock
from httpx import ASGITransport, AsyncClient
from typing import AsyncGenerator

# ── Session-level Global Mocks for heavy PyTorch models ──────────────────────
from app.services.embeddings.bge_m3_provider import BGEM3EmbeddingProvider
from app.services.speech.stt.faster_whisper_provider import FasterWhisperProvider
from app.services.speech.tts.melotts_provider import MeloTTSProvider
from app.services.speech.vad.silero_provider import SileroVADProvider

# 1. Mock BGEM3EmbeddingProvider to return 1024-dimensional mock vectors
async def mock_get_embeddings(self, texts):
    return [[0.1] * 1024 for _ in texts]
BGEM3EmbeddingProvider.get_embeddings = mock_get_embeddings

# 2. Mock FasterWhisperProvider to return a default test transcript
async def mock_transcribe(self, audio_bytes, language=None):
    return "hello reschedule please"
FasterWhisperProvider.transcribe_utterance = mock_transcribe

# 3. Mock MeloTTSProvider to yield mock G.711 mu-law chunks without file decoding
async def mock_stream_speech(self, text, cancel_event=None, language=None):
    for _ in range(5):
        yield b"\xff" * 160
MeloTTSProvider.stream_speech = mock_stream_speech

# 4. Mock SileroVADProvider model loaders to prevent Hub downloads
def mock_init(self):
    self.model = MagicMock()
    self.vad_iterator = MagicMock()
    self._accumulator = []
    self._in_speech = False
SileroVADProvider.__init__ = mock_init


# ── Session-level Global Database Mocking ───────────────────────────────────
# Decouple the test suite completely from real Supabase Postgres connections
class MagicMockResult:
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

class GlobalMockDbSession:
    async def execute(self, statement):
        from app.models.campaign import Campaign
        from app.models.customer import Customer
        from app.models.prompt_template import PromptTemplate
        from app.models.call_log import CallLog
        
        stmt_str = str(statement).lower()
        
        if "call_logs" in stmt_str:
            mock_call = CallLog(
                id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
                campaign_id=uuid.UUID("8b68301f-3ac4-4043-9f15-c56d98df904e"),
                customer_id=uuid.UUID("780a3d85-c788-46d6-a370-6602a6771bc6"),
                plivo_call_uuid="test_plivo_call_uuid_vad",
                status="ringing"
            )
            # Support test_websocket_audio_stream call Sid
            if "test_call_sid_123" in stmt_str or "test_call_sid" in stmt_str:
                mock_call.plivo_call_uuid = "test_call_sid_123"
            return MagicMockResult([mock_call])
            
        elif "prompt_templates" in stmt_str:
            mock_temp = PromptTemplate(
                id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
                campaign_id=uuid.UUID("8b68301f-3ac4-4043-9f15-c56d98df904e"),
                name="Mock Template",
                system_prompt="Greeting system instructions",
                is_active=True
            )
            return MagicMockResult([mock_temp])
            
        elif "customers" in stmt_str:
            mock_cust = Customer(
                id=uuid.UUID("780a3d85-c788-46d6-a370-6602a6771bc6"),
                first_name="Akash",
                last_name="Sharma",
                phone_number="+1234567890",
                is_active=True
            )
            return MagicMockResult([mock_cust])
            
        return MagicMockResult([])

    async def get(self, model, id_val):
        from app.models.campaign import Campaign
        from app.models.customer import Customer
        name = str(model.__name__)
        if name == "Campaign":
            return Campaign(id=uuid.UUID(str(id_val)), name="Outbound", workflow_type="hospital", is_active=True)
        elif name == "Customer":
            return Customer(id=uuid.UUID(str(id_val)), first_name="Akash", phone_number="+1234567890", is_active=True)
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

async def mock_get_db_session():
    yield GlobalMockDbSession()

# Apply the global get_db_session mock override to main app
from app.main import app
from app.db.session import get_db_session

# Reset/Apply overrides automatically on every test function run to prevent clear() bypass
@pytest.fixture(autouse=True)
def apply_global_overrides():
    app.dependency_overrides[get_db_session] = mock_get_db_session

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
