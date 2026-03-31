import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models.schemas import TokenResponse, UserCreate, UserLogin, UserResponse
from services.user_service import UserService
from utils.jwt_utils import create_access_token

router = APIRouter(prefix="/auth", tags=["Auth"])
logger = logging.getLogger(__name__)


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=201,
    summary="Register a new user",
    description="Creates a new account with role **user**. Admins are pre-seeded at startup.",
)
def register(data: UserCreate, db: Session = Depends(get_db)):
    service = UserService(db)
    user = service.register(data)
    return user


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=200,
    summary="Login — returns a JWT Bearer token",
    description=(
        "Login with username + password.\n\n"
        "**Copy the `access_token` value** and paste it into the "
        "Swagger **Authorize 🔒** dialog (prefix: `Bearer <token>`) "
        "or set it as a Bearer token in Postman."
    ),
)
def login(data: UserLogin, db: Session = Depends(get_db)):
    service = UserService(db)
    user = service.login(data.username, data.password)
    token = create_access_token(
        user_id=user.id,
        username=user.username,
        role=user.role.value,
    )
    return TokenResponse(
        message="Login successful",
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        username=user.username,
        role=user.role.value,
        expires_in_minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    )
