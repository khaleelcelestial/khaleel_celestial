"""
FastAPI security dependencies.

get_current_user  — any authenticated user (user OR admin)
get_current_admin — only users whose role == "admin"

Both work by reading the Bearer token from the Authorization header.
Swagger UI will show a 🔒 lock button on every protected endpoint.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from database import get_db
from models.db_models import User
from models.enums import UserRole
from utils.jwt_utils import decode_access_token

# HTTPBearer makes Swagger show the Authorize 🔒 button
bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    description="Paste the JWT token returned by POST /auth/login",
)


def _get_user_from_token(
    credentials: HTTPAuthorizationCredentials,
    db: Session,
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": "InvalidCredentialsError", "message": "Token is invalid or expired.", "status_code": 401},
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(credentials.credentials)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Dependency — returns the authenticated User (any role)."""
    return _get_user_from_token(credentials, db)


def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Dependency — returns the authenticated User only if role == admin."""
    user = _get_user_from_token(credentials, db)
    if user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "ForbiddenError", "message": "Only admins can access this endpoint.", "status_code": 403},
        )
    return user
