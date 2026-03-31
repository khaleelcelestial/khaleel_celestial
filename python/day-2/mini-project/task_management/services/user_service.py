import hashlib
import logging
from typing import Any, Dict, List
from datetime import datetime, timezone

from repositories.base_repository import BaseRepository
from models.schemas import UserCreate, UserLogin
from exceptions.custom_exceptions import (
    UserNotFoundError,
    DuplicateUserError,
    InvalidCredentialsError,
)

logger = logging.getLogger("user_service")


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


class UserService:
    """
    Handles all user-related business logic.
    Depends on BaseRepository abstraction (DIP), not on JSONRepository directly.
    """

    def __init__(self, repository: BaseRepository) -> None:
        self._repo = repository

    def register(self, payload: UserCreate) -> Dict[str, Any]:
        existing = self._repo.find_by_field("username", payload.username)  # type: ignore[attr-defined]
        if existing:
            logger.warning("Duplicate username: '%s'", payload.username)
            raise DuplicateUserError(payload.username)

        user = {
            "username": payload.username,
            "email": payload.email,
            "password": _hash_password(payload.password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        saved = self._repo.save(user)
        logger.info("User '%s' registered", payload.username)
        return saved

    def login(self, payload: UserLogin) -> Dict[str, Any]:
        user = self._repo.find_by_field("username", payload.username)  # type: ignore[attr-defined]
        if not user or user["password"] != _hash_password(payload.password):
            logger.warning("Failed login attempt for username: '%s'", payload.username)
            raise InvalidCredentialsError()
        logger.info("User '%s' logged in", payload.username)
        return user

    def list_users(self) -> List[Dict[str, Any]]:
        return self._repo.find_all()

    def delete_user(self, user_id: int) -> None:
        deleted = self._repo.delete(user_id)
        if not deleted:
            logger.error("User ID %d not found", user_id)
            raise UserNotFoundError(user_id)
        logger.info("User ID %d deleted", user_id)
