import logging
from fastapi import APIRouter, Depends, Query, status
from typing import List, Optional

from models.schemas import TaskCreate, TaskUpdate, TaskResponse
from models.enums import TaskStatus, TaskPriority
from services.task_service import TaskService
from repositories.json_repository import JSONRepository
from config import settings

logger = logging.getLogger("task_router")

router = APIRouter(prefix="/tasks", tags=["Tasks"])


# ── Dependency factory ────────────────────────────────────────────────────────

def get_task_service() -> TaskService:
    """Inject a TaskService backed by the JSON repository (DIP via Depends)."""
    repo = JSONRepository(settings.tasks_file, "tasks")
    return TaskService(repo)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    service: TaskService = Depends(get_task_service),
):
    """Create a new task."""
    return service.create_task(payload)


@router.get("", response_model=List[TaskResponse], status_code=status.HTTP_200_OK)
def list_tasks(
    status_filter: Optional[TaskStatus] = Query(default=None, alias="status"),
    priority: Optional[TaskPriority] = Query(default=None),
    owner: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
    service: TaskService = Depends(get_task_service),
):
    """List tasks with optional filters and pagination."""
    return service.list_tasks(
        status=status_filter,
        priority=priority,
        owner=owner,
        page=page,
        limit=limit,
    )


@router.get("/{task_id}", response_model=TaskResponse, status_code=status.HTTP_200_OK)
def get_task(
    task_id: int,
    service: TaskService = Depends(get_task_service),
):
    """Retrieve a single task by id."""
    return service.get_task(task_id)


@router.put("/{task_id}", response_model=TaskResponse, status_code=status.HTTP_200_OK)
def full_update_task(
    task_id: int,
    payload: TaskCreate,
    service: TaskService = Depends(get_task_service),
):
    """Fully replace a task's fields."""
    return service.full_update(task_id, payload)


@router.patch("/{task_id}", response_model=TaskResponse, status_code=status.HTTP_200_OK)
def partial_update_task(
    task_id: int,
    payload: TaskUpdate,
    service: TaskService = Depends(get_task_service),
):
    """Partially update one or more task fields."""
    return service.partial_update(task_id, payload)


@router.delete("/{task_id}", status_code=status.HTTP_200_OK)
def delete_task(
    task_id: int,
    service: TaskService = Depends(get_task_service),
):
    """Permanently delete a task by id."""
    service.delete_task(task_id)
    return {"message": f"Task {task_id} deleted successfully"}
