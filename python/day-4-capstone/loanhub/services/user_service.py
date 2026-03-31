import logging

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from exceptions.custom_exceptions import (DuplicateUserError,
                                          InvalidCredentialsError,
                                          UserNotFoundError)
from models.db_models import User
from models.enums import UserRole
from models.schemas import UserCreate
from repositories.sqlalchemy_repository import SQLAlchemyRepository

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserService:
    """SRP: handles only user business logic (registration, login, lookup)."""

    def __init__(self, db: Session):
        # DIP: depends on abstraction (BaseRepository) via concrete implementation
        self._repo = SQLAlchemyRepository(User, db)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _hash_password(self, plain: str) -> str:
        return pwd_context.hash(plain)

    def _verify_password(self, plain: str, hashed: str) -> bool:
        return pwd_context.verify(plain, hashed)

    # ── Public API ────────────────────────────────────────────────────────────

    def register(self, data: UserCreate) -> User:
        # Check duplicates
        if self._repo.find_by(username=data.username):
            logger.warning(f"Duplicate username attempt: {data.username}")
            raise DuplicateUserError(f"Username '{data.username}' already exists.")
        if self._repo.find_by(email=data.email):
            logger.warning(f"Duplicate email attempt: {data.email}")
            raise DuplicateUserError(f"Email '{data.email}' already registered.")

        user = User(
            username=data.username,
            email=data.email,
            password=self._hash_password(data.password),
            phone=data.phone,
            monthly_income=data.monthly_income,
            role=UserRole.user,
        )
        saved = self._repo.save(user)
        logger.info(f"User registered: {saved.username} (id={saved.id})")
        return saved

    def login(self, username: str, password: str) -> User:
        user = self._repo.find_by(username=username)
        if not user:
            logger.error(f"Login attempt with non-existent username: {username}")
            raise InvalidCredentialsError()
        if not self._verify_password(password, user.password):
            logger.error(f"Login failed for user: {username}")
            raise InvalidCredentialsError()
        logger.info(f"User logged in: {username}")
        return user

    def get_by_id(self, user_id: int) -> User:
        user = self._repo.find(user_id)
        if not user:
            raise UserNotFoundError(f"User with id={user_id} not found.")
        return user

    def get_by_username(self, username: str) -> User:
        user = self._repo.find_by(username=username)
        if not user:
            raise UserNotFoundError(f"User '{username}' not found.")
        return user

    def seed_admin(self, username: str, password: str, email: str) -> None:
        if self._repo.find_by(username=username):
            logger.info("Admin user already exists.")
            return
        admin = User(
            username=username,
            email=email,
            password=self._hash_password(password),
            phone="0000000000",
            monthly_income=0,
            role=UserRole.admin,
        )
        self._repo.save(admin)
        logger.info("Admin user seeded successfully.")

    def count_all(self) -> int:
        return self._repo.count()
