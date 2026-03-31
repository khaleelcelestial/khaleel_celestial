import pytest
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import app

client = TestClient(app)


# ── Register ──────────────────────────────────────────────────────────────────

def test_register_user_success():
    payload = {"username": "testuser", "email": "test@example.com", "password": "password123"}
    resp = client.post("/users/register", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == "testuser"
    assert "password" not in data


def test_register_duplicate_user():
    payload = {"username": "dupuser", "email": "dup@example.com", "password": "password123"}
    client.post("/users/register", json=payload)
    resp = client.post("/users/register", json=payload)
    assert resp.status_code == 409
    assert resp.json()["error"] == "DuplicateUserError"


def test_register_short_username():
    payload = {"username": "ab", "email": "x@example.com", "password": "password123"}
    resp = client.post("/users/register", json=payload)
    assert resp.status_code == 422


def test_register_invalid_email():
    payload = {"username": "validuser", "email": "not-an-email", "password": "password123"}
    resp = client.post("/users/register", json=payload)
    assert resp.status_code == 422


def test_register_short_password():
    payload = {"username": "validuser2", "email": "v2@example.com", "password": "short"}
    resp = client.post("/users/register", json=payload)
    assert resp.status_code == 422


# ── Login ─────────────────────────────────────────────────────────────────────

def test_login_success():
    client.post("/users/register", json={
        "username": "loginuser", "email": "login@example.com", "password": "mypassword"
    })
    resp = client.post("/users/login", json={"username": "loginuser", "password": "mypassword"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "loginuser"


def test_login_wrong_password():
    client.post("/users/register", json={
        "username": "loginuser2", "email": "login2@example.com", "password": "correctpass"
    })
    resp = client.post("/users/login", json={"username": "loginuser2", "password": "wrongpass"})
    assert resp.status_code == 401
    assert resp.json()["error"] == "InvalidCredentialsError"


def test_login_nonexistent_user():
    resp = client.post("/users/login", json={"username": "ghost", "password": "nopassword"})
    assert resp.status_code == 401


# ── List / Delete ─────────────────────────────────────────────────────────────

def test_list_users():
    resp = client.get("/users")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_delete_user_not_found():
    resp = client.delete("/users/999999")
    assert resp.status_code == 404
    assert resp.json()["error"] == "UserNotFoundError"
