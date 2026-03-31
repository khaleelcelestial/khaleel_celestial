import logging
from sqlalchemy.orm import Session

from decorators.timer import timer
from models.db_models import Loan, User
from models.enums import LoanStatus
from models.schemas import AnalyticsSummary
from repositories.sqlalchemy_repository import SQLAlchemyRepository

logger = logging.getLogger(__name__)


class AnalyticsService:
    """SRP: handles only analytics/aggregation logic."""

    def __init__(self, db: Session):
        self._loan_repo = SQLAlchemyRepository(Loan, db)
        self._user_repo = SQLAlchemyRepository(User, db)

    @timer
    def get_summary(self) -> AnalyticsSummary:
        all_loans: list[Loan] = self._loan_repo.all_no_filter()
        total_users: int = self._user_repo.count()

        # Dictionary comprehension — status breakdown
        status_counts = {
            status.value: len([l for l in all_loans if l.status == status])
            for status in LoanStatus
        }

        # Dictionary comprehension — loans by purpose
        purposes = {l.purpose.value for l in all_loans}
        loans_by_purpose = {
            purpose: len([l for l in all_loans if l.purpose.value == purpose])
            for purpose in purposes
        }

        # Dictionary comprehension — loans by employment status
        emp_statuses = {l.employment_status.value for l in all_loans}
        loans_by_employment = {
            emp: len([l for l in all_loans if l.employment_status.value == emp])
            for emp in emp_statuses
        }

        # List comprehension — all amounts
        amounts = [l.amount for l in all_loans]
        avg_loan_amount = sum(amounts) / len(amounts) if amounts else 0.0

        # List comprehension with conditional — approved only
        total_disbursed = sum(
            l.amount for l in all_loans if l.status == LoanStatus.approved
        )

        return AnalyticsSummary(
            total_users=total_users,
            total_loans=len(all_loans),
            pending_loans=status_counts.get("pending", 0),
            approved_loans=status_counts.get("approved", 0),
            rejected_loans=status_counts.get("rejected", 0),
            total_disbursed_amount=total_disbursed,
            loans_by_purpose=loans_by_purpose,
            loans_by_employment=loans_by_employment,
            avg_loan_amount=round(avg_loan_amount, 2),
        )
