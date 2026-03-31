import pytest
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import app

client = TestClient(app)

TASK_PAYLOAD = {
    "title": "Test Task",
    "description": "A test task",
    "priority": "medium",
    "status": "pending",
    "owner": "alice",
}


def create_task(overrides=None):
    payload = {**TASK_PAYLOAD, **(overrides or {})}
    return client.post("/tasks", json=payload)


# ── Create ────────────────────────────────────────────────────────────────────

def test_create_task_success():
    resp = create_task()
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Test Task"
    assert data["owner"] == "alice"
    assert "id" in data


def test_create_task_short_title():
    resp = create_task({"title": "ab"})
    assert resp.status_code == 422


def test_create_task_invalid_priority():
    resp = create_task({"priority": "critical"})
    assert resp.status_code == 422


def test_create_task_invalid_status():
    resp = create_task({"status": "unknown"})
    assert resp.status_code == 422


def test_create_task_description_too_long():
    resp = create_task({"description": "x" * 501})
    assert resp.status_code == 422


# ── Get / List ────────────────────────────────────────────────────────────────

def test_get_task_by_id():
    created = create_task({"title": "Get Me"}).json()
    resp = client.get(f"/tasks/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_task_not_found():
    resp = client.get("/tasks/999999")
    assert resp.status_code == 404
    assert resp.json()["error"] == "TaskNotFoundError"


def test_list_tasks_returns_list():
    resp = client.get("/tasks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_tasks_filter_by_status():
    create_task({"title": "Pending Task", "status": "pending"})
    resp = client.get("/tasks?status=pending")
    assert resp.status_code == 200
    for task in resp.json():
        assert task["status"] == "pending"


def test_list_tasks_filter_by_priority():
    create_task({"title": "High Priority", "priority": "high"})
    resp = client.get("/tasks?priority=high")
    assert resp.status_code == 200
    for task in resp.json():
        assert task["priority"] == "high"


def test_list_tasks_filter_by_owner():
    create_task({"title": "Owner Filter Task", "owner": "charlie"})
    resp = client.get("/tasks?owner=charlie")
    assert resp.status_code == 200
    for task in resp.json():
        assert task["owner"] == "charlie"


def test_list_tasks_pagination():
    for i in range(5):
        create_task({"title": f"Page Task {i}"})
    resp = client.get("/tasks?page=1&limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


def test_list_tasks_page_beyond_data():
    resp = client.get("/tasks?page=9999&limit=10")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Update ────────────────────────────────────────────────────────────────────

def test_full_update_task():
    task_id = create_task({"title": "Old Title"}).json()["id"]
    updated = {**TASK_PAYLOAD, "title": "New Title", "status": "in_progress"}
    resp = client.put(f"/tasks/{task_id}", json=updated)
    assert resp.status_code == 200
    assert resp.json()["title"] == "New Title"
    assert resp.json()["status"] == "in_progress"


def test_partial_update_task():
    task_id = create_task({"title": "Patch Me"}).json()["id"]
    resp = client.patch(f"/tasks/{task_id}", json={"status": "completed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["title"] == "Patch Me"  # unchanged


def test_full_update_not_found():
    resp = client.put("/tasks/999999", json=TASK_PAYLOAD)
    assert resp.status_code == 404


def test_partial_update_not_found():
    resp = client.patch("/tasks/999999", json={"status": "completed"})
    assert resp.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────

def test_delete_task_success():
    task_id = create_task({"title": "Delete Me"}).json()["id"]
    resp = client.delete(f"/tasks/{task_id}")
    assert resp.status_code == 200
    assert client.get(f"/tasks/{task_id}").status_code == 404


def test_delete_task_not_found():
    resp = client.delete("/tasks/999999")
    assert resp.status_code == 404
    assert resp.json()["error"] == "TaskNotFoundError"
