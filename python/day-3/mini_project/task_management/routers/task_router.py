from fastapi import APIRouter, Depends, status, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from database import get_db
from services.task_service import TaskService
from models.schemas import TaskCreate, TaskUpdate, TaskResponse
from models.enums import TaskStatus, TaskPriority

router = APIRouter(prefix="/tasks", tags=["Tasks"])

NOTIFICATIONS_LOG = "notifications.log"


def log_task_notification(title: str, owner_id: int) -> None:
    """Background task: appends a notification entry to notifications.log."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = f"[{timestamp}] Task '{title}' created by user_id={owner_id} — notification sent\n"
    with open(NOTIFICATIONS_LOG, "a", encoding="utf-8") as f:
        f.write(entry)


@router.post("/", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    data: TaskCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    service = TaskService(db)
    task = service.create_task(data)
    # Fire-and-forget background notification
    background_tasks.add_task(log_task_notification, task.title, task.owner_id)
    return task


@router.get("/", response_model=List[TaskResponse])
def list_tasks(
    status: Optional[TaskStatus] = None,
    priority: Optional[TaskPriority] = None,
    owner_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    service = TaskService(db)
    return service.list_tasks(status=status, priority=priority, owner_id=owner_id)


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: int, db: Session = Depends(get_db)):
    service = TaskService(db)
    return service.get_task(task_id)


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(task_id: int, data: TaskUpdate, db: Session = Depends(get_db)):
    service = TaskService(db)
    return service.update_task(task_id, data)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    service = TaskService(db)
    service.delete_task(task_id)
