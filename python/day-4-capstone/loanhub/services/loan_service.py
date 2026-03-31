import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from decorators.timer import timer
from exceptions.custom_exceptions import (ForbiddenError, InvalidLoanReviewError,
                                          LoanNotFoundError, MaxPendingLoansError)
from models.db_models import Loan
from models.enums import LoanStatus, UserRole
from models.schemas import LoanCreate, LoanReview
from repositories.sqlalchemy_repository import SQLAlchemyRepository

logger = logging.getLogger(__name__)

MAX_PENDING = 3


class LoanService:
    """SRP: handles only loan business logic."""

    def __init__(self, db: Session):
        self._repo = SQLAlchemyRepository(Loan, db)

    # ── User actions ──────────────────────────────────────────────────────────

    @timer
    def apply_loan(self, data: LoanCreate, current_user) -> Loan:
        if current_user.role == UserRole.admin:
            raise ForbiddenError("Admins cannot apply for loans.")

        pending_count = self._repo.count(user_id=current_user.id, status=LoanStatus.pending)
        if pending_count >= MAX_PENDING:
            logger.warning(f"User {current_user.username} reached max pending loans.")
            raise MaxPendingLoansError()

        loan = Loan(
            user_id=current_user.id,
            amount=data.amount,
            purpose=data.purpose,
            tenure_months=data.tenure_months,
            employment_status=data.employment_status,
            status=LoanStatus.pending,
        )
        saved = self._repo.save(loan)
        logger.info(
            f"Loan #{saved.id} applied by user '{current_user.username}' "
            f"for {data.purpose} — ₹{data.amount}"
        )
        return saved

    def get_my_loans(
        self,
        current_user,
        status: Optional[str] = None,
        page: int = 1,
        limit: int = 10,
    ) -> list[Loan]:
        filters: dict = {"user_id": current_user.id}
        if status:
            filters["status"] = LoanStatus(status)
        return self._repo.find_all_filtered(
            filters=filters, page=page, limit=limit, sort_by="applied_at", order="desc"
        )

    def get_my_loan_by_id(self, loan_id: int, current_user) -> Loan:
        loan = self._repo.find(loan_id)
        if not loan or loan.user_id != current_user.id:
            raise LoanNotFoundError(f"Loan #{loan_id} not found.")
        return loan

    # ── Admin actions ─────────────────────────────────────────────────────────

    def get_all_loans(
        self,
        current_user,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        purpose: Optional[str] = None,
        employment_status: Optional[str] = None,
        page: int = 1,
        limit: int = 10,
        sort_by: str = "applied_at",
        order: str = "desc",
    ) -> list[Loan]:
        if current_user.role != UserRole.admin:
            raise ForbiddenError()

        filters: dict = {}
        if status:
            filters["status"] = LoanStatus(status)
        if user_id:
            filters["user_id"] = user_id
        if purpose:
            filters["purpose"] = purpose
        if employment_status:
            filters["employment_status"] = employment_status

        return self._repo.find_all_filtered(
            filters=filters, page=page, limit=limit, sort_by=sort_by, order=order
        )

    def get_loan_by_id_admin(self, loan_id: int, current_user) -> Loan:
        if current_user.role != UserRole.admin:
            raise ForbiddenError()
        loan = self._repo.find(loan_id)
        if not loan:
            raise LoanNotFoundError(f"Loan #{loan_id} not found.")
        return loan

    @timer
    def review_loan(self, loan_id: int, data: LoanReview, current_user) -> Loan:
        if current_user.role != UserRole.admin:
            raise ForbiddenError()

        loan = self._repo.find(loan_id)
        if not loan:
            raise LoanNotFoundError(f"Loan #{loan_id} not found.")
        if loan.status != LoanStatus.pending:
            logger.warning(f"Re-review attempt on Loan #{loan_id} (status={loan.status})")
            raise InvalidLoanReviewError(
                f"Loan #{loan_id} has already been {loan.status}. Only pending loans can be reviewed."
            )

        updated = self._repo.update(
            loan,
            status=data.status,
            admin_remarks=data.admin_remarks,
            reviewed_by=current_user.username,
            reviewed_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        logger.info(
            f"Loan #{loan_id} {data.status} by admin '{current_user.username}'"
        )
        return updated

    def get_loan_for_notification(self, loan_id: int) -> Optional[Loan]:
        return self._repo.find(loan_id)

    def get_all_loans_raw(self) -> list[Loan]:
        return self._repo.all_no_filter()