"""
Shared pytest fixtures for LoanHub tests.
Uses an in-memory SQLite database — no real Postgres connection needed.
All protected endpoints are called with JWT Bearer tokens.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from main import app

SQLALCHEMY_TEST_URL = "sqlite:///./test_loanhub.db"

engine = create_engine(
    SQLALCHEMY_TEST_URL, connect_args={"check_same_thread": False}
)


# SQLite doesn't have schemas — patch search_path event to be a no-op
@event.listens_for(engine, "connect")
def _noop_search_path(dbapi_conn, connection_record):
    pass


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Patch db_models to drop schema for SQLite ────────────────────────────────
# SQLite doesn't support schemas so we clear __table_args__ before creating tables.
from models import db_models as _dm
_dm.User.__table__.schema = None
_dm.Loan.__table__.schema = None
# Fix FK reference too
for fk in _dm.Loan.__table__.foreign_keys:
    fk._table_key = "users"  # sqlite needs simple name


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    import os
    if os.path.exists("test_loanhub.db"):
        os.remove("test_loanhub.db")


@pytest.fixture(scope="session")
def client(setup_db):
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ── Helper: get Bearer header from login response ────────────────────────────

def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def registered_user(client):
    """Register a regular test user and return their data + token."""
    reg = client.post("/auth/register", json={
        "username": "testuser",
        "email": "testuser@test.com",
        "password": "password123",
        "phone": "9876543210",
        "monthly_income": 50000,
    })
    assert reg.status_code == 201

    login = client.post("/auth/login", json={
        "username": "testuser",
        "password": "password123",
    })
    assert login.status_code == 200
    data = login.json()
    return {**reg.json(), "token": data["access_token"]}


@pytest.fixture(scope="session")
def admin_user(client):
    """Create admin directly in DB and return their data + token."""
    from passlib.context import CryptContext
    from models.db_models import User
    from models.enums import UserRole

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    db = TestingSessionLocal()
    existing = db.query(User).filter(User.username == "admin").first()
    if not existing:
        admin = User(
            username="admin",
            email="admin@loanhub.com",
            password=pwd.hash("admin1234"),
            phone="0000000000",
            monthly_income=0,
            role=UserRole.admin,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
    db.close()

    login = client.post("/auth/login", json={
        "username": "admin",
        "password": "admin1234",
    })
    assert login.status_code == 200
    data = login.json()
    return {"id": data["user_id"], "username": data["username"],
            "role": data["role"], "token": data["access_token"]}

