import logging
from fastapi import APIRouter, Depends, status
from typing import List

from models.schemas import UserCreate, UserLogin, UserResponse
from services.user_service import UserService
from repositories.json_repository import JSONRepository
from config import settings

logger = logging.getLogger("user_router")

router = APIRouter(prefix="/users", tags=["Users"])


# ── Dependency factory ────────────────────────────────────────────────────────

def get_user_service() -> UserService:
    """Inject a UserService backed by the JSON repository (DIP via Depends)."""
    repo = JSONRepository(settings.users_file, "users")
    return UserService(repo)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(
    payload: UserCreate,
    service: UserService = Depends(get_user_service),
):
    """Register a new user account."""
    return service.register(payload)


@router.post("/login", status_code=status.HTTP_200_OK)
def login_user(
    payload: UserLogin,
    service: UserService = Depends(get_user_service),
):
    """Validate credentials and return basic user info."""
    user = service.login(payload)
    return {"message": "Login successful", "username": user["username"], "id": user["id"]}


@router.get("", response_model=List[UserResponse], status_code=status.HTTP_200_OK)
def list_users(service: UserService = Depends(get_user_service)):
    """Return all registered users (passwords excluded)."""
    return service.list_users()


@router.delete("/{user_id}", status_code=status.HTTP_200_OK)
def delete_user(
    user_id: int,
    service: UserService = Depends(get_user_service),
):
    """Permanently delete a user by id."""
    service.delete_user(user_id)
    return {"message": f"User {user_id} deleted successfully"}
