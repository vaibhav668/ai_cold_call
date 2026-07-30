import pytest
import jwt
from httpx import AsyncClient
from datetime import datetime, timezone, timedelta
from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.services.security import hash_password, verify_password, create_access_token
from app.models.user import User

# Dummy user credentials
test_user_id = "11111111-1111-1111-1111-111111111111"
test_email = "test@example.com"
test_password = "securepassword"
test_hashed_password = hash_password(test_password)

@pytest.fixture
def mock_user():
    now = datetime.now(timezone.utc)
    return User(
        id=test_user_id,
        email=test_email,
        hashed_password=test_hashed_password,
        role="agent",
        is_active=True,
        created_at=now,
        updated_at=now
    )

@pytest.fixture
def mock_admin():
    now = datetime.now(timezone.utc)
    return User(
        id="22222222-2222-2222-2222-222222222222",
        email="admin@example.com",
        hashed_password=test_hashed_password,
        role="admin",
        is_active=True,
        created_at=now,
        updated_at=now
    )

# Mock databases dependencies for tests
class MockDbSession:
    def __init__(self, users=None, tokens=None):
        self.users = users or {}
        self.tokens = tokens or {}

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
        
        stmt_str = str(statement)
        if "users.email" in stmt_str:
            return Result(list(self.users.values()))
        elif "refresh_tokens.token" in stmt_str:
            return Result(list(self.tokens.values()))
        return Result([])

    async def get(self, model, id):
        if str(model.__name__) == "User":
            return self.users.get(str(id))
        elif str(model.__name__) == "RefreshToken":
            return self.tokens.get(str(id))
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

@pytest.mark.anyio
async def test_password_hashing():
    password = "plainpassword"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("wrongpassword", hashed) is False

@pytest.mark.anyio
async def test_jwt_generation_and_decoding():
    user_id = test_user_id
    role = "agent"
    token = create_access_token(user_id, role, expires_delta=timedelta(minutes=5))
    
    from app.services.security import decode_access_token
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == user_id
    assert payload["role"] == role
    assert payload["type"] == "access"

@pytest.mark.anyio
async def test_login_success(client: AsyncClient, mock_user, monkeypatch):
    # Set up DB Session Override
    db_session = MockDbSession(users={test_user_id: mock_user})
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db

    # Call login endpoint
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": test_email, "password": test_password}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    
    app.dependency_overrides.clear()

@pytest.mark.anyio
async def test_get_profile_authenticated(client: AsyncClient, mock_user):
    async def mock_current_user():
        return mock_user
    
    app.dependency_overrides[get_current_user] = mock_current_user
    
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == test_email
    assert data["role"] == "agent"
    
    app.dependency_overrides.clear()

@pytest.mark.anyio
async def test_rbac_restriction(client: AsyncClient, mock_user):
    async def mock_current_user():
        return mock_user
        
    app.dependency_overrides[get_current_user] = mock_current_user
    
    response = await client.post(
        "/api/v1/users/",
        json={"email": "newuser@example.com", "password": "newpassword", "role": "agent"}
    )
    assert response.status_code == 403
    assert "Not enough privileges" in response.json()["detail"]
    
    app.dependency_overrides.clear()

@pytest.mark.anyio
async def test_rbac_allowed_for_admin(client: AsyncClient, mock_admin, monkeypatch):
    async def mock_current_user():
        return mock_admin
        
    db_session = MockDbSession()
    async def mock_db():
        yield db_session
        
    app.dependency_overrides[get_current_user] = mock_current_user
    app.dependency_overrides[get_db_session] = mock_db
    
    from app.repositories.user import UserRepository
    async def mock_create(self, obj):
        obj.id = "33333333-3333-3333-3333-333333333333"
        obj.created_at = datetime.now(timezone.utc)
        obj.updated_at = datetime.now(timezone.utc)
        return obj
        
    monkeypatch.setattr(UserRepository, "create", mock_create)
    
    response = await client.post(
        "/api/v1/users/",
        json={"email": "newuser@example.com", "password": "newpassword", "role": "agent"}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "newuser@example.com"
    assert data["role"] == "agent"
    
    app.dependency_overrides.clear()
