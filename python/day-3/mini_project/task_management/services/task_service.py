from typing import List, Optional
from sqlalchemy.orm import Session

from repositories.sqlalchemy_repository import SQLAlchemyTaskRepository, SQLAlchemyUserRepository
from models.db_models import Task
from models.schemas import TaskCreate, TaskUpdate, TaskResponse
from models.enums import TaskStatus, TaskPriority
from exceptions.custom_exceptions import TaskNotFoundError, UserNotFoundError


class TaskService:
    def __init__(self, db: Session):
        # DIP: service depends on BaseRepository abstraction
        self.repo = SQLAlchemyTaskRepository(db)
        self.user_repo = SQLAlchemyUserRepository(db)

    def create_task(self, data: TaskCreate) -> TaskResponse:
        # Validate the owner exists
        owner = self.user_repo.find(data.owner_id)
        if not owner:
            raise UserNotFoundError(data.owner_id)

        task = Task(
            title=data.title,
            description=data.description,
            status=data.status,
            priority=data.priority,
            owner_id=data.owner_id,
        )
        saved = self.repo.save(task)
        return TaskResponse.model_validate(saved)

    def get_task(self, task_id: int) -> TaskResponse:
        task = self.repo.find(task_id)
        if not task:
            raise TaskNotFoundError(task_id)
        return TaskResponse.model_validate(task)

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        owner_id: Optional[int] = None,
    ) -> List[TaskResponse]:
        filters = {}
        if status is not None:
            filters["status"] = status
        if priority is not None:
            filters["priority"] = priority
        if owner_id is not None:
            filters["owner_id"] = owner_id

        tasks = self.repo.find_all(filters or None)
        return [TaskResponse.model_validate(t) for t in tasks]

    def update_task(self, task_id: int, data: TaskUpdate) -> TaskResponse:
        # Only pass non-None fields
        update_data = data.model_dump(exclude_none=True)
        task = self.repo.update(task_id, update_data)
        if not task:
            raise TaskNotFoundError(task_id)
        return TaskResponse.model_validate(task)

    def delete_task(self, task_id: int) -> None:
        deleted = self.repo.delete(task_id)
        if not deleted:
            raise TaskNotFoundError(task_id)
