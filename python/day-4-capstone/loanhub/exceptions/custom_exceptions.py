from fastapi import Request
from fastapi.responses import JSONResponse


# ─── Custom Exception Classes ─────────────────────────────────────────────────

class UserNotFoundError(Exception):
    def __init__(self, message: str = "User not found."):
        self.message = message
        super().__init__(self.message)


class DuplicateUserError(Exception):
    def __init__(self, message: str = "Username or email already exists."):
        self.message = message
        super().__init__(self.message)


class InvalidCredentialsError(Exception):
    def __init__(self, message: str = "Invalid username or password."):
        self.message = message
        super().__init__(self.message)


class ForbiddenError(Exception):
    def __init__(self, message: str = "You do not have permission to perform this action."):
        self.message = message
        super().__init__(self.message)


class LoanNotFoundError(Exception):
    def __init__(self, message: str = "Loan not found."):
        self.message = message
        super().__init__(self.message)


class MaxPendingLoansError(Exception):
    def __init__(self, message: str = "You already have 3 pending loans. Wait for review before applying again."):
        self.message = message
        super().__init__(self.message)


class InvalidLoanReviewError(Exception):
    def __init__(self, message: str = "Only pending loans can be reviewed."):
        self.message = message
        super().__init__(self.message)


# ─── Exception Handlers ───────────────────────────────────────────────────────

def _error_response(error_type: str, message: str, status_code: int):
    return JSONResponse(
        status_code=status_code,
        content={"error": error_type, "message": message, "status_code": status_code},
    )


async def user_not_found_handler(request: Request, exc: UserNotFoundError):
    return _error_response("UserNotFoundError", exc.message, 404)


async def duplicate_user_handler(request: Request, exc: DuplicateUserError):
    return _error_response("DuplicateUserError", exc.message, 409)


async def invalid_credentials_handler(request: Request, exc: InvalidCredentialsError):
    return _error_response("InvalidCredentialsError", exc.message, 401)


async def forbidden_handler(request: Request, exc: ForbiddenError):
    return _error_response("ForbiddenError", exc.message, 403)


async def loan_not_found_handler(request: Request, exc: LoanNotFoundError):
    return _error_response("LoanNotFoundError", exc.message, 404)


async def max_pending_loans_handler(request: Request, exc: MaxPendingLoansError):
    return _error_response("MaxPendingLoansError", exc.message, 422)


async def invalid_loan_review_handler(request: Request, exc: InvalidLoanReviewError):
    return _error_response("InvalidLoanReviewError", exc.message, 422)


def register_exception_handlers(app):
    app.add_exception_handler(UserNotFoundError, user_not_found_handler)
    app.add_exception_handler(DuplicateUserError, duplicate_user_handler)
    app.add_exception_handler(InvalidCredentialsError, invalid_credentials_handler)
    app.add_exception_handler(ForbiddenError, forbidden_handler)
    app.add_exception_handler(LoanNotFoundError, loan_not_found_handler)
    app.add_exception_handler(MaxPendingLoansError, max_pending_loans_handler)
    app.add_exception_handler(InvalidLoanReviewError, invalid_loan_review_handler)
