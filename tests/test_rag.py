import pytest
import uuid
import pypdf
from datetime import datetime, timezone
from httpx import AsyncClient
from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.campaign import Campaign
from app.models.user import User
from app.repositories.base import BaseRepository
from app.services.rag_service import chunk_text

test_user_id = "22222222-2222-2222-2222-222222222222"
mock_admin_user = User(id=test_user_id, email="admin@example.com", role="admin", is_active=True)

class MockDbSession:
    def __init__(self, campaigns=None, documents=None):
        self.campaigns = campaigns or {}
        self.documents = documents or {}

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
        return Result(list(self.documents.values()))

    async def get(self, model, id):
        name = str(model.__name__)
        if name == "Campaign":
            return self.campaigns.get(str(id))
        elif name == "Document":
            return self.documents.get(str(id))
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
async def test_text_chunking():
    text = "Hello world. This is a simple text that we want to partition into character segments."
    chunks = chunk_text(text, chunk_size=20, chunk_overlap=5)
    assert len(chunks) > 0
    assert all(isinstance(c, str) for c in chunks)

@pytest.mark.anyio
async def test_upload_txt_document_success(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    campaign_id = uuid.uuid4()
    campaign = Campaign(id=campaign_id, name="Test Campaign", workflow_type="hospital")
    
    db_session = MockDbSession(campaigns={str(campaign_id): campaign})
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db
    
    from app.services.rag_service import RAGService
    async def mock_index(self, campaign_id, document_id, filename, text):
        return 3
    monkeypatch.setattr(RAGService, "index_document", mock_index)
    
    files = {"file": ("kb.txt", b"This is a text content for the vector database.", "text/plain")}
    response = await client.post(f"/api/v1/campaigns/{campaign_id}/documents", files=files)
    assert response.status_code == 201
    data = response.json()
    assert data["filename"] == "kb.txt"
    assert data["file_type"] == "txt"
    assert data["status"] == "active"
    assert data["total_chunks"] == 3

@pytest.mark.anyio
async def test_upload_pdf_document_success(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    campaign_id = uuid.uuid4()
    campaign = Campaign(id=campaign_id, name="Test Campaign", workflow_type="hospital")
    
    db_session = MockDbSession(campaigns={str(campaign_id): campaign})
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db
    
    class MockPage:
        def extract_text(self):
            return "This is a mock PDF document text page content."
    class MockPdfReader:
        def __init__(self, stream):
            self.pages = [MockPage()]
    monkeypatch.setattr(pypdf, "PdfReader", MockPdfReader)

    from app.services.rag_service import RAGService
    async def mock_index(self, campaign_id, document_id, filename, text):
        return 1
    monkeypatch.setattr(RAGService, "index_document", mock_index)
    
    files = {"file": ("kb.pdf", b"%PDF-1.4 dummy contents", "application/pdf")}
    response = await client.post(f"/api/v1/campaigns/{campaign_id}/documents", files=files)
    assert response.status_code == 201
    data = response.json()
    assert data["filename"] == "kb.pdf"
    assert data["file_type"] == "pdf"
    assert data["status"] == "active"
    assert data["total_chunks"] == 1

@pytest.mark.anyio
async def test_semantic_search_success(client: AsyncClient, override_auth, monkeypatch):
    campaign_id = uuid.uuid4()
    mock_results = [
        {
            "text": "This is a matching chunk from Qdrant",
            "score": 0.88,
            "document_id": uuid.uuid4(),
            "filename": "kb.txt",
            "chunk_index": 2
        }
    ]
    
    from app.services.rag_service import RAGService
    async def mock_search(self, campaign_id, query, limit=5):
        return mock_results
    monkeypatch.setattr(RAGService, "search_knowledge", mock_search)
    
    response = await client.get(f"/api/v1/campaigns/{campaign_id}/search", params={"q": "test query", "limit": 5})
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "test query"
    assert len(data["results"]) == 1
    assert data["results"][0]["text"] == "This is a matching chunk from Qdrant"
    assert data["results"][0]["score"] == 0.88

@pytest.mark.anyio
async def test_chroma_indexing_and_searching(monkeypatch):
    import tempfile
    import shutil
    from app.services.rag_service import RAGService
    from app.db.chroma import chroma_manager
    from app.core.config import settings
    from app.services.embeddings.bge_m3_provider import BGEM3EmbeddingProvider
    
    # Mock embedding generator to prevent loading model/calling API during tests
    async def mock_get_embeddings(self, texts):
        return [[0.1] * 1024 for _ in texts]
    monkeypatch.setattr(BGEM3EmbeddingProvider, "get_embeddings", mock_get_embeddings)
    
    # Set in-memory path for ephemeral client
    monkeypatch.setattr(settings, "CHROMA_DB_PATH", ":memory:")
    
    # Reset connection pool client
    chroma_manager._client = None
    chroma_manager.connect()
    
    rag = RAGService()
    campaign_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    
    chunks_count = await rag.index_document(
        campaign_id=campaign_id,
        document_id=doc_id,
        filename="test.txt",
        text="This is a test document to index in ChromaDB."
    )
    assert chunks_count > 0
    
    results = await rag.search_knowledge(campaign_id=campaign_id, query="test document", limit=1)
    assert len(results) > 0
    assert "test" in results[0]["text"].lower()
