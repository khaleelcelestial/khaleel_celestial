import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from main import app

# Use an in-memory SQLite database for tests
SQLITE_URL = "sqlite:///./test_temp.db"

engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app)


def test_create_user(client):
    response = client.post("/users/", json={
        "username": "alice",
        "email": "alice@example.com",
        "full_name": "Alice Smith",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "alice"
    assert data["email"] == "alice@example.com"
    assert "id" in data


def test_duplicate_username(client):
    client.post("/users/", json={"username": "bob", "email": "bob@example.com"})
    response = client.post("/users/", json={"username": "bob", "email": "bob2@example.com"})
    assert response.status_code == 409


def test_duplicate_email(client):
    client.post("/users/", json={"username": "carol", "email": "carol@example.com"})
    response = client.post("/users/", json={"username": "carol2", "email": "carol@example.com"})
    assert response.status_code == 409


def test_get_user(client):
    create_resp = client.post("/users/", json={"username": "dave", "email": "dave@example.com"})
    user_id = create_resp.json()["id"]
    response = client.get(f"/users/{user_id}")
    assert response.status_code == 200
    assert response.json()["username"] == "dave"


def test_get_user_not_found(client):
    response = client.get("/users/9999")
    assert response.status_code == 404


def test_list_users(client):
    client.post("/users/", json={"username": "eve", "email": "eve@example.com"})
    client.post("/users/", json={"username": "frank", "email": "frank@example.com"})
    response = client.get("/users/")
    assert response.status_code == 200
    assert len(response.json()) >= 2


def test_delete_user(client):
    create_resp = client.post("/users/", json={"username": "grace", "email": "grace@example.com"})
    user_id = create_resp.json()["id"]
    del_resp = client.delete(f"/users/{user_id}")
    assert del_resp.status_code == 204
    get_resp = client.get(f"/users/{user_id}")
    assert get_resp.status_code == 404
