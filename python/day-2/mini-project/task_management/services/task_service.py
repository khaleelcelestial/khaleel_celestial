import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from repositories.base_repository import BaseRepository
from models.schemas import TaskCreate, TaskUpdate
from models.enums import TaskStatus, TaskPriority
from exceptions.custom_exceptions import TaskNotFoundError

logger = logging.getLogger("task_service")


class TaskService:
    """
    Handles all task-related business logic.
    Depends on BaseRepository abstraction (DIP), not on JSONRepository directly.
    """

    def __init__(self, repository: BaseRepository) -> None:
        self._repo = repository

    def create_task(self, payload: TaskCreate) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        task = {
            "title": payload.title,
            "description": payload.description,
            "status": payload.status.value,
            "priority": payload.priority.value,
            "owner": payload.owner,
            "created_at": now,
            "updated_at": now,
        }
        saved = self._repo.save(task)
        logger.info("Task '%s' created with id %d", payload.title, saved["id"])
        return saved

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        owner: Optional[str] = None,
        page: int = 1,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        tasks = self._repo.find_all()

        if status:
            tasks = [t for t in tasks if t.get("status") == status.value]
        if priority:
            tasks = [t for t in tasks if t.get("priority") == priority.value]
        if owner:
            tasks = [t for t in tasks if t.get("owner") == owner]

        start = (page - 1) * limit
        return tasks[start: start + limit]

    def get_task(self, task_id: int) -> Dict[str, Any]:
        task = self._repo.find_by_id(task_id)
        if not task:
            logger.error("Task ID %d not found", task_id)
            raise TaskNotFoundError(task_id)
        return task

    def full_update(self, task_id: int, payload: TaskCreate) -> Dict[str, Any]:
        existing = self._repo.find_by_id(task_id)
        if not existing:
            logger.error("Task ID %d not found", task_id)
            raise TaskNotFoundError(task_id)

        updated_data = {
            "title": payload.title,
            "description": payload.description,
            "status": payload.status.value,
            "priority": payload.priority.value,
            "owner": payload.owner,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        result = self._repo.update(task_id, updated_data)
        logger.info("Task ID %d fully updated", task_id)
        return result  # type: ignore[return-value]

    def partial_update(self, task_id: int, payload: TaskUpdate) -> Dict[str, Any]:
        existing = self._repo.find_by_id(task_id)
        if not existing:
            logger.error("Task ID %d not found", task_id)
            raise TaskNotFoundError(task_id)

        patch: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if payload.title is not None:
            patch["title"] = payload.title
        if payload.description is not None:
            patch["description"] = payload.description
        if payload.status is not None:
            patch["status"] = payload.status.value
        if payload.priority is not None:
            patch["priority"] = payload.priority.value
        if payload.owner is not None:
            patch["owner"] = payload.owner

        result = self._repo.update(task_id, patch)
        logger.info("Task ID %d partially updated", task_id)
        return result  # type: ignore[return-value]

    def delete_task(self, task_id: int) -> None:
        deleted = self._repo.delete(task_id)
        if not deleted:
            logger.error("Task ID %d not found", task_id)
            raise TaskNotFoundError(task_id)
        logger.info("Task ID %d deleted", task_id)
