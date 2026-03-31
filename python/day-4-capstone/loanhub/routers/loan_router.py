import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from models.db_models import User
from models.schemas import LoanCreate, LoanResponse
from services.loan_service import LoanService
from utils.dependencies import get_current_user
from utils.notifications import background_loan_applied

router = APIRouter(prefix="/loans", tags=["Loans — User"])
logger = logging.getLogger(__name__)


@router.post(
    "",
    response_model=LoanResponse,
    status_code=201,
    summary="Apply for a loan",
    description="Submit a new loan application. Requires a valid **user** JWT token.",
)
def apply_loan(
    data: LoanCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = LoanService(db)
    loan = service.apply_loan(data, current_user)
    background_tasks.add_task(
        background_loan_applied, loan.id, current_user.username,
        loan.purpose.value, loan.amount
    )
    return loan


@router.get(
    "/my",
    response_model=list[LoanResponse],
    status_code=200,
    summary="List my loan applications",
    description="Returns all loans belonging to the authenticated user. Supports `status` filter and pagination.",
)
def get_my_loans(
    status: Optional[str] = Query(None, description="Filter by status: pending | approved | rejected"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = LoanService(db)
    return service.get_my_loans(current_user, status=status, page=page, limit=limit)


@router.get(
    "/my/{loan_id}",
    response_model=LoanResponse,
    status_code=200,
    summary="Get a single loan detail",
    description="Returns full detail of one loan. Returns 404 if the loan doesn't belong to you.",
)
def get_my_loan(
    loan_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = LoanService(db)
    return service.get_my_loan_by_id(loan_id, current_user)
