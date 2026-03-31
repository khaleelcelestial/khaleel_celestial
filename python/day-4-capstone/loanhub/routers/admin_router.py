import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from decorators.timer import timer
from models.db_models import Loan, User
from models.schemas import BulkCheckRequest, BulkCheckResult, LoanResponse, LoanReview
from services.loan_service import LoanService
from services.user_service import UserService
from utils.dependencies import get_current_admin
from utils.notifications import background_loan_reviewed

router = APIRouter(prefix="/admin", tags=["Admin — Loan Management"])
logger = logging.getLogger(__name__)


@router.get(
    "/loans",
    response_model=list[LoanResponse],
    status_code=200,
    summary="List all loans (admin only)",
    description="Returns every loan across all users. Supports filtering by status, user_id, purpose, employment_status, plus pagination and sorting.",
)
def list_all_loans(
    status: Optional[str] = Query(None, description="pending | approved | rejected"),
    user_id_filter: Optional[int] = Query(None, alias="user_id", description="Filter by applicant user ID"),
    purpose: Optional[str] = Query(None, description="personal | education | home | vehicle | business"),
    employment_status: Optional[str] = Query(None, description="employed | self_employed | unemployed | student"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    sort_by: str = Query("applied_at"),
    order: str = Query("desc", description="asc | desc"),
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    service = LoanService(db)
    return service.get_all_loans(
        current_user,
        status=status,
        user_id=user_id_filter,
        purpose=purpose,
        employment_status=employment_status,
        page=page,
        limit=limit,
        sort_by=sort_by,
        order=order,
    )


@router.get(
    "/loans/{loan_id}",
    response_model=LoanResponse,
    status_code=200,
    summary="Get any loan detail (admin only)",
    description="Returns full detail of any loan by ID.",
)
def get_loan_detail(
    loan_id: int,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    service = LoanService(db)
    return service.get_loan_by_id_admin(loan_id, current_user)


@router.patch(
    "/loans/{loan_id}/review",
    response_model=LoanResponse,
    status_code=200,
    summary="Approve or reject a loan (admin only)",
    description=(
        "Review a **pending** loan. Status must be `approved` or `rejected`. "
        "`admin_remarks` is mandatory (5–500 chars). "
        "Already-reviewed loans cannot be re-reviewed."
    ),
)
def review_loan(
    loan_id: int,
    data: LoanReview,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    service = LoanService(db)
    loan = service.review_loan(loan_id, data, current_user)
    applicant = UserService(db).get_by_id(loan.user_id)
    background_tasks.add_task(
        background_loan_reviewed, loan.id, applicant.username, loan.status.value
    )
    return loan


# ── Threading: bulk eligibility check ─────────────────────────────────────────

def _compute_eligibility(loan: Loan, user_income: int) -> BulkCheckResult:
    """Compute income-to-loan ratio as eligibility score (IO-simulated)."""
    import time
    time.sleep(0.01)
    if loan is None:
        return BulkCheckResult(loan_id=-1, error="Loan not found")
    score = round(user_income / loan.amount * 100, 2) if loan.amount > 0 else 0.0
    return BulkCheckResult(loan_id=loan.id, eligibility_score=score)


@router.post(
    "/loans/bulk-check",
    response_model=list[BulkCheckResult],
    status_code=200,
    summary="Bulk eligibility check (admin only)",
    description="Concurrently computes income-to-loan eligibility scores for a list of loan IDs using ThreadPoolExecutor.",
)
@timer
def bulk_eligibility_check(
    body: BulkCheckRequest,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    service = LoanService(db)
    user_svc = UserService(db)
    results: list[BulkCheckResult] = []
    futures_map = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        for lid in body.loan_ids:
            all_loans = service.get_all_loans_raw()
            loan_obj = next((l for l in all_loans if l.id == lid), None)
            income = 0
            if loan_obj:
                try:
                    applicant = user_svc.get_by_id(loan_obj.user_id)
                    income = applicant.monthly_income
                except Exception:
                    income = 0
            future = executor.submit(_compute_eligibility, loan_obj, income)
            futures_map[future] = lid

        for future in as_completed(futures_map):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(BulkCheckResult(loan_id=futures_map[future], error=str(exc)))

    return results
