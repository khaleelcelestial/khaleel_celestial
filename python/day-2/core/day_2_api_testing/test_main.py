from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# =========================
# 1. TEST GET ENDPOINT
# =========================
def test_read_root():
    response = client.get("/")
    
    assert response.status_code == 200
    assert response.json() == {"message": "Hello"}


# =========================
# 2. TEST POST (VALID DATA)
# =========================
def test_create_user_valid():
    payload = {
        "email": "test@gmail.com",
        "age": 25
    }

    response = client.post("/users", json=payload)

    assert response.status_code == 200
    assert response.json() == payload


# =========================
# 3. TEST INVALID EMAIL
# =========================
def test_create_user_invalid_email():
    payload = {
        "email": "wrong-email",
        "age": 25
    }

    response = client.post("/users", json=payload)

    assert response.status_code == 422


# =========================
# 4. TEST INVALID AGE
# =========================
def test_create_user_invalid_age():
    payload = {
        "email": "test@gmail.com",
        "age": -5
    }

    response = client.post("/users", json=payload)

    assert response.status_code == 422


# =========================
# 5. TEST MISSING FIELD
# =========================
def test_create_user_missing_field():
    payload = {
        "email": "test@gmail.com"
    }

    response = client.post("/users", json=payload)

    assert response.status_code == 422