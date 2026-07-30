import pytest
from httpx import AsyncClient
from app.main import app
from app.core.config import settings

@pytest.mark.anyio
async def test_settings():
    assert settings.APP_NAME == "AI Voice Calling Platform"
    assert settings.DEBUG is True

@pytest.mark.anyio
async def test_health_endpoint(client: AsyncClient, monkeypatch):
    from app.db.chroma import chroma_manager
    
    # Mock ChromaDB heartbeat checks
    class MockChromaClient:
        def heartbeat(self):
            return 12345678
            
    monkeypatch.setattr(chroma_manager, "get_client", lambda: MockChromaClient())
    
    from app.db.session import get_db_session
    class MockDbSession:
        async def execute(self, statement):
            return None
        async def rollback(self):
            pass
        async def close(self):
            pass
            
    async def mock_db_session():
        yield MockDbSession()
        
    app.dependency_overrides[get_db_session] = mock_db_session
    
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["services"]["database"] == "healthy"
    assert data["services"]["chromadb"] == "healthy"
    
    app.dependency_overrides.clear()
