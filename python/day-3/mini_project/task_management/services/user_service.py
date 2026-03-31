from typing import List, Optional
from sqlalchemy.orm import Session

from repositories.sqlalchemy_repository import SQLAlchemyUserRepository
from models.db_models import User
from models.schemas import UserCreate, UserResponse
from exceptions.custom_exceptions import UserNotFoundError, DuplicateUserError


class UserService:
    def __init__(self, db: Session):
        # DIP: service depends on BaseRepository abstraction via SQLAlchemy impl
        self.repo = SQLAlchemyUserRepository(db)

    def create_user(self, data: UserCreate) -> UserResponse:
        # Check for duplicates before attempting insert
        if self.repo.find_by_username(data.username):
            raise DuplicateUserError("username", data.username)
        if self.repo.find_by_email(data.email):
            raise DuplicateUserError("email", data.email)

        user = User(
            username=data.username,
            email=data.email,
            full_name=data.full_name,
        )
        saved = self.repo.save(user)
        return UserResponse.model_validate(saved)

    def get_user(self, user_id: int) -> UserResponse:
        user = self.repo.find(user_id)
        if not user:
            raise UserNotFoundError(user_id)
        return UserResponse.model_validate(user)

    def list_users(self) -> List[UserResponse]:
        users = self.repo.find_all()
        return [UserResponse.model_validate(u) for u in users]

    def delete_user(self, user_id: int) -> None:
        deleted = self.repo.delete(user_id)
        if not deleted:
            raise UserNotFoundError(user_id)
