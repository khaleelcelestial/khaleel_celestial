import pytest
from fastapi.testclient import TestClient
from Q16 import app                     # ← import your FastAPI app

client = TestClient(app)                 # ← fake Postman in Python


# ─── VALID TASK PAYLOAD ───────────────────────────────
valid_task = {
    "title"      : "Write report",
    "description": "Q2 summary",
    "status"     : "pending"
}


# ─── 1. HEALTH CHECK ──────────────────────────────────
def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


# ─── 2. CREATE TASK — VALID ───────────────────────────
def test_create_task():
    response = client.post("/tasks", json=valid_task)

    assert response.status_code == 201                  # created ✅
    assert response.json()["title"]       == "Write report"
    assert response.json()["description"] == "Q2 summary"
    assert response.json()["status"]      == "pending"
    assert "id" in response.json()                      # id exists ✅


# ─── 3. CREATE TASK — INVALID STATUS ─────────────────
def test_create_task_invalid_status():
    bad_payload = {
        "title"      : "Bad Task",
        "description": "test",
        "status"     : "wrong_status"    # ← not in enum ❌
    }
    response = client.post("/tasks", json=bad_payload)

    assert response.status_code == 422                  # validation error ✅


# ─── 4. CREATE TASK — MISSING FIELDS ─────────────────
def test_create_task_missing_fields():
    bad_payload = {
        "title": "Only Title"            # missing description ❌
    }
    response = client.post("/tasks", json=bad_payload)

    assert response.status_code == 422                  # validation error ✅


# ─── 5. GET ALL TASKS ─────────────────────────────────
def test_get_tasks():
    response = client.get("/tasks")

    assert response.status_code == 200
    assert isinstance(response.json(), list)            # returns list ✅


# ─── 6. GET TASKS WITH STATUS FILTER ─────────────────
def test_get_tasks_with_filter():
    # create a task first
    client.post("/tasks", json=valid_task)

    response = client.get("/tasks?status=pending")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
    for task in response.json():
        assert task["status"] == "pending"              # all pending ✅


# ─── 7. GET SINGLE TASK — FOUND ───────────────────────
def test_get_task_by_id():
    # create task first
    create_response = client.post("/tasks", json=valid_task)
    task_id         = create_response.json()["id"]     # get id

    response = client.get(f"/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json()["id"]    == task_id
    assert response.json()["title"] == "Write report"


# ─── 8. GET SINGLE TASK — NOT FOUND ──────────────────
def test_get_task_not_found():
    response = client.get("/tasks/99999")               # non existent id

    assert response.status_code == 404                  # not found ✅
    assert response.json()["error"]   == "TaskNotFoundError"
    assert response.json()["message"] == "Task with id 99999 not found"
    assert "status_code" in response.json()


# ─── 9. UPDATE TASK — VALID ───────────────────────────
def test_update_task():
    # create task first
    create_response = client.post("/tasks", json=valid_task)
    task_id         = create_response.json()["id"]

    # update only status
    update_payload = {"status": "completed"}
    response       = client.put(f"/tasks/{task_id}", json=update_payload)

    assert response.status_code == 200
    assert response.json()["status"] == "completed"    # updated ✅
    assert response.json()["title"]  == "Write report" # unchanged ✅


# ─── 10. UPDATE TASK — NOT FOUND ─────────────────────
def test_update_task_not_found():
    update_payload = {"status": "completed"}
    response       = client.put("/tasks/99999", json=update_payload)

    assert response.status_code == 404                  # not found ✅
    assert response.json()["error"] == "TaskNotFoundError"


# ─── 11. DELETE TASK — VALID ──────────────────────────
def test_delete_task():
    # create task first
    create_response = client.post("/tasks", json=valid_task)
    task_id         = create_response.json()["id"]

    # delete it
    delete_response = client.delete(f"/tasks/{task_id}")
    assert delete_response.status_code == 200
    assert "deleted successfully" in delete_response.json()["message"]

    # confirm it's gone
    get_response = client.get(f"/tasks/{task_id}")
    assert get_response.status_code == 404              # gone ✅


# ─── 12. DELETE TASK — NOT FOUND ─────────────────────
def test_delete_task_not_found():
    response = client.delete("/tasks/99999")

    assert response.status_code == 404                  # not found ✅
    assert response.json()["error"] == "TaskNotFoundError"


# ─── 13. DUPLICATE TITLE ──────────────────────────────
def test_create_duplicate_task():
    # create once
    client.post("/tasks", json=valid_task)

    # create again with same title
    response = client.post("/tasks", json=valid_task)

    assert response.status_code == 409                  # conflict ✅
    assert response.json()["error"] == "TaskAlreadyExistsError"