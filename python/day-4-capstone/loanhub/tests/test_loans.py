"""
test_loans.py — 5 tests for user loan endpoints (JWT-protected).
"""
from tests.conftest import auth_header


def test_apply_loan_success(client, registered_user):
    """Apply for a loan with a valid JWT → 201."""
    resp = client.post(
        "/loans",
        headers=auth_header(registered_user["token"]),
        json={
            "amount": 200000,
            "purpose": "education",
            "tenure_months": 36,
            "employment_status": "employed",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["amount"] == 200000
    assert body["purpose"] == "education"
    assert body["status"] == "pending"
    assert body["user_id"] == registered_user["id"]


def test_apply_loan_amount_too_high(client, registered_user):
    """Apply with amount > ₹10,00,000 → 422."""
    resp = client.post(
        "/loans",
        headers=auth_header(registered_user["token"]),
        json={
            "amount": 1500000,
            "purpose": "home",
            "tenure_months": 120,
            "employment_status": "employed",
        },
    )
    assert resp.status_code == 422


def test_apply_loan_max_pending(client):
    """Apply when already at 3 pending loans → 422 MaxPendingLoansError."""
    # Register fresh user
    reg = client.post("/auth/register", json={
        "username": "pendinguser",
        "email": "pendinguser@test.com",
        "password": "password123",
        "phone": "9111111111",
        "monthly_income": 30000,
    })
    assert reg.status_code == 201

    login = client.post("/auth/login", json={"username": "pendinguser", "password": "password123"})
    token = login.json()["access_token"]
    headers = auth_header(token)

    payload = {"amount": 50000, "purpose": "personal",
                "tenure_months": 12, "employment_status": "employed"}

    for _ in range(3):
        r = client.post("/loans", headers=headers, json=payload)
        assert r.status_code == 201

    resp = client.post("/loans", headers=headers, json=payload)
    assert resp.status_code == 422
    assert resp.json()["error"] == "MaxPendingLoansError"


def test_get_my_loans(client, registered_user):
    """GET /loans/my with valid token → 200 list."""
    resp = client.get("/loans/my", headers=auth_header(registered_user["token"]))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1


def test_get_single_loan_detail(client, registered_user):
    """GET /loans/my/{id} → 200 with correct fields."""
    loans = client.get("/loans/my", headers=auth_header(registered_user["token"])).json()
    loan_id = loans[0]["id"]
    resp = client.get(f"/loans/my/{loan_id}", headers=auth_header(registered_user["token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == loan_id
    assert body["user_id"] == registered_user["id"]
    assert "status" in body

