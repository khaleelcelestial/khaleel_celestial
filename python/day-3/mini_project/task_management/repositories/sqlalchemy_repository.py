from typing import Optional, List, Any, Dict, Type, TypeVar
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from repositories.base_repository import BaseRepository
from models.db_models import User, Task
from models.enums import TaskStatus, TaskPriority
from exceptions.custom_exceptions import DuplicateUserError

T = TypeVar("T")


class SQLAlchemyUserRepository(BaseRepository[User]):
    """
    SQLAlchemy-backed repository for User entities.
    Implements the same BaseRepository interface as the JSON repository,
    so services never need to change when the storage layer is swapped.
    """

    def __init__(self, db: Session):
        self.db = db

    def save(self, entity: User) -> User:
        try:
            self.db.add(entity)
            self.db.commit()
            self.db.refresh(entity)
            return entity
        except IntegrityError as e:
            self.db.rollback()
            err_str = str(e.orig)
            if "username" in err_str:
                raise DuplicateUserError("username", entity.username)
            if "email" in err_str:
                raise DuplicateUserError("email", entity.email)
            raise

    def find(self, entity_id: int) -> Optional[User]:
        return self.db.query(User).filter(User.id == entity_id).first()

    def find_by_username(self, username: str) -> Optional[User]:
        return self.db.query(User).filter(User.username == username).first()

    def find_by_email(self, email: str) -> Optional[User]:
        return self.db.query(User).filter(User.email == email).first()

    def find_all(self, filters: Optional[Dict[str, Any]] = None) -> List[User]:
        query = self.db.query(User)
        if filters:
            for key, value in filters.items():
                if hasattr(User, key) and value is not None:
                    query = query.filter(getattr(User, key) == value)
        return query.all()

    def update(self, entity_id: int, data: Dict[str, Any]) -> Optional[User]:
        user = self.find(entity_id)
        if not user:
            return None
        for key, value in data.items():
            if value is not None and hasattr(user, key):
                setattr(user, key, value)
        self.db.commit()
        self.db.refresh(user)
        return user

    def delete(self, entity_id: int) -> bool:
        user = self.find(entity_id)
        if not user:
            return False
        self.db.delete(user)
        self.db.commit()
        return True


class SQLAlchemyTaskRepository(BaseRepository[Task]):
    """
    SQLAlchemy-backed repository for Task entities.
    Implements the same BaseRepository interface as the JSON repository.
    """

    def __init__(self, db: Session):
        self.db = db

    def save(self, entity: Task) -> Task:
        self.db.add(entity)
        self.db.commit()
        self.db.refresh(entity)
        return entity

    def find(self, entity_id: int) -> Optional[Task]:
        return self.db.query(Task).filter(Task.id == entity_id).first()

    def find_all(self, filters: Optional[Dict[str, Any]] = None) -> List[Task]:
        query = self.db.query(Task)
        if filters:
            for key, value in filters.items():
                if value is None:
                    continue
                if key == "status" and isinstance(value, str):
                    value = TaskStatus(value)
                if key == "priority" and isinstance(value, str):
                    value = TaskPriority(value)
                if hasattr(Task, key):
                    query = query.filter(getattr(Task, key) == value)
        return query.all()

    def update(self, entity_id: int, data: Dict[str, Any]) -> Optional[Task]:
        task = self.find(entity_id)
        if not task:
            return None
        for key, value in data.items():
            if value is not None and hasattr(task, key):
                setattr(task, key, value)
        self.db.commit()
        self.db.refresh(task)
        return task

    def delete(self, entity_id: int) -> bool:
        task = self.find(entity_id)
        if not task:
            return False
        self.db.delete(task)
        self.db.commit()
        return True
