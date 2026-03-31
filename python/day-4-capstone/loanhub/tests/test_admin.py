"""
test_admin.py — 5 tests for admin loan management (JWT-protected).
"""
from tests.conftest import auth_header


def _get_pending_loan_id(client, admin_user) -> int:
    resp = client.get(
        "/admin/loans",
        headers=auth_header(admin_user["token"]),
        params={"status": "pending"},
    )
    assert resp.status_code == 200
    loans = resp.json()
    assert len(loans) >= 1, "No pending loans available for admin tests."
    return loans[0]["id"]


def test_admin_views_all_loans(client, admin_user):
    """Admin views all loans with valid admin JWT → 200 list."""
    resp = client.get("/admin/loans", headers=auth_header(admin_user["token"]))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_admin_approves_loan(client, admin_user):
    """Admin approves a pending loan → 200, status=approved."""
    loan_id = _get_pending_loan_id(client, admin_user)
    resp = client.patch(
        f"/admin/loans/{loan_id}/review",
        headers=auth_header(admin_user["token"]),
        json={"status": "approved", "admin_remarks": "Good income-to-loan ratio. Approved."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["reviewed_by"] == admin_user["username"]
    assert body["admin_remarks"] is not None


def test_admin_rejects_loan(client, admin_user):
    """Admin rejects a pending loan → 200, status=rejected."""
    loan_id = _get_pending_loan_id(client, admin_user)
    resp = client.patch(
        f"/admin/loans/{loan_id}/review",
        headers=auth_header(admin_user["token"]),
        json={"status": "rejected", "admin_remarks": "Insufficient income for the requested amount."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["admin_remarks"] is not None


def test_admin_rereview_already_reviewed(client, admin_user):
    """Re-reviewing an approved/rejected loan → 422 InvalidLoanReviewError."""
    resp = client.get(
        "/admin/loans",
        headers=auth_header(admin_user["token"]),
        params={"status": "approved"},
    )
    assert resp.status_code == 200
    loans = resp.json()
    assert len(loans) >= 1, "No approved loans to re-review."
    loan_id = loans[0]["id"]

    resp2 = client.patch(
        f"/admin/loans/{loan_id}/review",
        headers=auth_header(admin_user["token"]),
        json={"status": "rejected", "admin_remarks": "Trying to re-review this loan."},
    )
    assert resp2.status_code == 422
    assert resp2.json()["error"] == "InvalidLoanReviewError"


def test_non_admin_cannot_access_admin_endpoint(client, registered_user):
    """Regular user JWT on admin endpoint → 403 ForbiddenError."""
    resp = client.get("/admin/loans", headers=auth_header(registered_user["token"]))
    assert resp.status_code == 403

