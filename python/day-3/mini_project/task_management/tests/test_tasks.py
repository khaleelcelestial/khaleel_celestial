import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from main import app

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


@pytest.fixture()
def user_id(client):
    resp = client.post("/users/", json={"username": "testuser", "email": "test@example.com"})
    return resp.json()["id"]


def test_create_task(client, user_id):
    response = client.post("/tasks/", json={
        "title": "Fix the bug",
        "description": "Reproduce and fix the login bug",
        "status": "pending",
        "priority": "high",
        "owner_id": user_id,
    })
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Fix the bug"
    assert data["owner_id"] == user_id


def test_create_task_unknown_user(client):
    response = client.post("/tasks/", json={
        "title": "Orphan task",
        "owner_id": 9999,
    })
    assert response.status_code == 404


def test_get_task(client, user_id):
    create_resp = client.post("/tasks/", json={"title": "My Task", "owner_id": user_id})
    task_id = create_resp.json()["id"]
    response = client.get(f"/tasks/{task_id}")
    assert response.status_code == 200
    assert response.json()["title"] == "My Task"


def test_get_task_not_found(client):
    response = client.get("/tasks/9999")
    assert response.status_code == 404


def test_list_tasks_no_filter(client, user_id):
    client.post("/tasks/", json={"title": "Task A", "owner_id": user_id})
    client.post("/tasks/", json={"title": "Task B", "owner_id": user_id})
    response = client.get("/tasks/")
    assert response.status_code == 200
    assert len(response.json()) >= 2


def test_list_tasks_filter_by_status(client, user_id):
    client.post("/tasks/", json={"title": "Pending Task", "status": "pending", "owner_id": user_id})
    client.post("/tasks/", json={"title": "Done Task", "status": "completed", "owner_id": user_id})
    response = client.get("/tasks/?status=pending")
    assert response.status_code == 200
    for task in response.json():
        assert task["status"] == "pending"


def test_list_tasks_filter_by_priority(client, user_id):
    client.post("/tasks/", json={"title": "Critical Task", "priority": "critical", "owner_id": user_id})
    client.post("/tasks/", json={"title": "Low Task", "priority": "low", "owner_id": user_id})
    response = client.get("/tasks/?priority=critical")
    assert response.status_code == 200
    for task in response.json():
        assert task["priority"] == "critical"


def test_update_task(client, user_id):
    create_resp = client.post("/tasks/", json={"title": "Old Title", "owner_id": user_id})
    task_id = create_resp.json()["id"]
    response = client.patch(f"/tasks/{task_id}", json={"title": "New Title", "status": "in_progress"})
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "New Title"
    assert data["status"] == "in_progress"


def test_update_task_not_found(client):
    response = client.patch("/tasks/9999", json={"title": "Ghost"})
    assert response.status_code == 404


def test_delete_task(client, user_id):
    create_resp = client.post("/tasks/", json={"title": "Delete Me", "owner_id": user_id})
    task_id = create_resp.json()["id"]
    del_resp = client.delete(f"/tasks/{task_id}")
    assert del_resp.status_code == 204
    get_resp = client.get(f"/tasks/{task_id}")
    assert get_resp.status_code == 404


def test_delete_task_not_found(client):
    response = client.delete("/tasks/9999")
    assert response.status_code == 404
