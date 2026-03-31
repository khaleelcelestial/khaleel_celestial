import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator, Field

from models.enums import EmploymentStatus, LoanPurpose, LoanStatus, UserRole


# ─── User Schemas ────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str
    password: str = Field(..., min_length=8)
    phone: str
    monthly_income: int = Field(..., ge=0)

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_]{3,50}$", v):
            raise ValueError("Username must be 3–50 chars, alphanumeric and underscores only")
        return v

    @field_validator("email")
    @classmethod
    def email_valid(cls, v: str) -> str:
        if "@" not in v or "." not in v:
            raise ValueError("Email must contain @ and .")
        return v

    @field_validator("phone")
    @classmethod
    def phone_valid(cls, v: str) -> str:
        if not re.match(r"^\d{10,15}$", v):
            raise ValueError("Phone must be 10–15 digits only")
        return v


class UserLogin(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    message: str
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    role: str
    expires_in_minutes: int


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    phone: str
    monthly_income: int
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ─── Loan Schemas ─────────────────────────────────────────────────────────────

class LoanCreate(BaseModel):
    amount: int = Field(..., gt=0, le=1000000)
    purpose: LoanPurpose
    tenure_months: int = Field(..., ge=6, le=360)
    employment_status: EmploymentStatus


class LoanReview(BaseModel):
    status: LoanStatus
    admin_remarks: str = Field(..., min_length=5, max_length=500)

    @field_validator("status")
    @classmethod
    def status_not_pending(cls, v: LoanStatus) -> LoanStatus:
        if v == LoanStatus.pending:
            raise ValueError("Review status must be 'approved' or 'rejected', not 'pending'")
        return v


class LoanResponse(BaseModel):
    id: int
    user_id: int
    amount: int
    purpose: str
    tenure_months: int
    employment_status: str
    status: str
    admin_remarks: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    applied_at: datetime
    updated_at: datetime
    credit_score: Optional[int] = None

    model_config = {"from_attributes": True}


# ─── Bulk Check Schema ────────────────────────────────────────────────────────

class BulkCheckRequest(BaseModel):
    loan_ids: list[int]


class BulkCheckResult(BaseModel):
    loan_id: int
    eligibility_score: Optional[float] = None
    error: Optional[str] = None


# ─── Analytics Schema ─────────────────────────────────────────────────────────

class AnalyticsSummary(BaseModel):
    total_users: int
    total_loans: int
    pending_loans: int
    approved_loans: int
    rejected_loans: int
    total_disbursed_amount: int
    loans_by_purpose: dict
    loans_by_employment: dict
    avg_loan_amount: float
