import pytest
import io
import openpyxl
import uuid
from datetime import datetime, timezone
from httpx import AsyncClient
from app.main import app
from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models.customer import Customer
from app.models.user import User
from app.repositories.base import BaseRepository
from app.repositories.customer import CustomerRepository

test_user_id = "22222222-2222-2222-2222-222222222222"
mock_admin_user = User(id=test_user_id, email="admin@example.com", role="admin", is_active=True)

# Mock DB Session
class MockDbSession:
    def __init__(self, customers=None, imports=None):
        self.customers = customers or {}
        self.imports = imports or {}

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
        if "customers.phone_number" in stmt_str:
            return Result(list(self.customers.values()))
        elif "import_history" in stmt_str:
            return Result(list(self.imports.values()))
        return Result([])

    async def get(self, model, id):
        if str(model.__name__) == "Customer":
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

def generate_mock_excel(rows):
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.append(["first_name", "last_name", "phone_number", "email", "age"])
    for r in rows:
        sheet.append(r)
    file_stream = io.BytesIO()
    wb.save(file_stream)
    wb.close()
    return file_stream.getvalue()

@pytest.mark.anyio
async def test_create_customer_invalid_phone(client: AsyncClient, override_auth):
    response = await client.post(
        "/api/v1/customers/",
        json={"first_name": "John", "phone_number": "invalid-phone", "email": "john@example.com"}
    )
    assert response.status_code == 422

@pytest.mark.anyio
async def test_create_customer_success(client: AsyncClient, override_auth, mock_repos):
    db_session = MockDbSession()
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db
    
    response = await client.post(
        "/api/v1/customers/",
        json={"first_name": "Jane", "phone_number": "+1234567890", "email": "jane@example.com", "custom_variables": {"age": 28}}
    )
    assert response.status_code == 201
    data = response.json()
    assert data["first_name"] == "Jane"
    assert data["phone_number"] == "+1234567890"
    assert data["custom_variables"]["age"] == 28
    assert data["id"] is not None

@pytest.mark.anyio
async def test_import_csv(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    db_session = MockDbSession()
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db

    async def mock_get_by_phone(self, phone):
        return None
    monkeypatch.setattr(CustomerRepository, "get_by_phone", mock_get_by_phone)

    csv_content = (
        "first_name,last_name,phone_number,email,city\n"
        "Alice,Smith,+1987654321,alice@example.com,New York\n"
        "Bob,Jones,invalid-phone,bob@example.com,Chicago\n"
        "Charlie,Brown,+1987654321,charlie@example.com,Dallas\n"
    )

    files = {"file": ("customers.csv", csv_content.encode("utf-8"), "text/csv")}
    response = await client.post("/api/v1/customers/import", files=files)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial"
    assert data["total_records"] == 3
    assert data["successfully_imported"] == 1
    assert data["failed_records"] == 2
    errors = data["error_details"]
    assert len(errors) == 2
    assert errors[0]["row"] == 3
    assert "Invalid E.164 phone" in errors[0]["error"]
    assert errors[1]["row"] == 4
    assert "Duplicate phone number in file" in errors[1]["error"]

@pytest.mark.anyio
async def test_import_excel(client: AsyncClient, override_auth, mock_repos, monkeypatch):
    db_session = MockDbSession()
    async def mock_db():
        yield db_session
    app.dependency_overrides[get_db_session] = mock_db

    async def mock_get_by_phone(self, phone):
        return None
    monkeypatch.setattr(CustomerRepository, "get_by_phone", mock_get_by_phone)

    excel_rows = [
        ["David", "Miller", "+1777888999", "david@example.com", 35],
        ["Eve", "Davis", "+1555666777", "eve@example.com", 40]
    ]
    excel_content = generate_mock_excel(excel_rows)

    files = {"file": ("customers.xlsx", excel_content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    response = await client.post("/api/v1/customers/import", files=files)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["total_records"] == 2
    assert data["successfully_imported"] == 2
    assert data["failed_records"] == 0
