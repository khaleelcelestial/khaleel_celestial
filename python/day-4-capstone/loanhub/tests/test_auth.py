"""
test_auth.py — 5 tests for registration and login endpoints.
"""


def test_register_user_success(client):
    """Register a new user successfully (201)."""
    resp = client.post("/auth/register", json={
        "username": "newuser1",
        "email": "newuser1@test.com",
        "password": "securepass1",
        "phone": "9123456780",
        "monthly_income": 40000,
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "newuser1"
    assert body["email"] == "newuser1@test.com"
    assert body["role"] == "user"
    assert "password" not in body


def test_register_duplicate_username(client, registered_user):
    """Register with duplicate username → 409."""
    resp = client.post("/auth/register", json={
        "username": registered_user["username"],
        "email": "different@test.com",
        "password": "password123",
        "phone": "9000000001",
        "monthly_income": 30000,
    })
    assert resp.status_code == 409
    assert resp.json()["error"] == "DuplicateUserError"


def test_register_invalid_email(client):
    """Register with invalid email format → 422."""
    resp = client.post("/auth/register", json={
        "username": "emailfailuser",
        "email": "not-an-email",
        "password": "password123",
        "phone": "9000000002",
        "monthly_income": 30000,
    })
    assert resp.status_code == 422


def test_login_success_returns_jwt(client, registered_user):
    """Login with correct credentials → 200 with access_token."""
    resp = client.post("/auth/login", json={
        "username": registered_user["username"],
        "password": "password123",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "Login successful"
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["username"] == registered_user["username"]
    assert body["role"] == "user"
    assert body["expires_in_minutes"] > 0


def test_login_wrong_password(client, registered_user):
    """Login with wrong password → 401."""
    resp = client.post("/auth/login", json={
        "username": registered_user["username"],
        "password": "wrongpassword",
    })
    assert resp.status_code == 401
    assert resp.json()["error"] == "InvalidCredentialsError"

