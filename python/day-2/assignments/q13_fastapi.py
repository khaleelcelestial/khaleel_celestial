from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, field_validator
from typing import Optional, List
from enum import Enum

app = FastAPI()


# ─── ENUM FOR STATUS ──────────────────────────────────
class TaskStatus(str, Enum):
    pending     = "pending"
    in_progress = "in_progress"
    completed   = "completed"


# ─── PYDANTIC MODELS ──────────────────────────────────
class TaskCreate(BaseModel):             # what client SENDS
    title      : str
    description: str
    status     : TaskStatus = TaskStatus.pending   # default pending


class TaskUpdate(BaseModel):             # what client sends to UPDATE
    title      : Optional[str]       = None
    description: Optional[str]       = None
    status     : Optional[TaskStatus]= None


class TaskResponse(BaseModel):           # what server RETURNS
    id         : int
    title      : str
    description: str
    status     : TaskStatus


# ─── IN MEMORY STORAGE ────────────────────────────────
tasks          = []
task_id_counter = 1


# ─── HEALTH CHECK ─────────────────────────────────────
@app.get("/health")
def health_check():
    return {"status": "healthy"}


# ─── CREATE TASK ──────────────────────────────────────
@app.post("/tasks", response_model=TaskResponse,
          status_code=status.HTTP_201_CREATED)      # ← returns 201
def create_task(task: TaskCreate):
    global task_id_counter

    new_task = {
        "id"         : task_id_counter,
        "title"      : task.title,
        "description": task.description,
        "status"     : task.status
    }
    tasks.append(new_task)
    task_id_counter += 1
    return new_task


# ─── LIST ALL TASKS ───────────────────────────────────
@app.get("/tasks", response_model=List[TaskResponse])
def get_tasks(status: Optional[TaskStatus] = None):  # optional filter
    if status:
        return [t for t in tasks if t["status"] == status]
    return tasks                                      # return all


# ─── GET SINGLE TASK ──────────────────────────────────
@app.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: int):
    for task in tasks:
        if task["id"] == task_id:
            return task
    raise HTTPException(
        status_code=404,
        detail=f"Task with id {task_id} not found"  # ← 404 ✅
    )


# ─── UPDATE TASK (PUT) ────────────────────────────────
@app.put("/tasks/{task_id}", response_model=TaskResponse)
def update_task(task_id: int, updated: TaskUpdate):
    for task in tasks:
        if task["id"] == task_id:
            if updated.title       is not None:
                task["title"]       = updated.title
            if updated.description is not None:
                task["description"] = updated.description
            if updated.status      is not None:
                task["status"]      = updated.status
            return task
    raise HTTPException(
        status_code=404,
        detail=f"Task with id {task_id} not found"
    )


# ─── DELETE TASK ──────────────────────────────────────
@app.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    for task in tasks:
        if task["id"] == task_id:
            tasks.remove(task)
            return {
                "message": f"Task {task_id} deleted successfully"
            }
    raise HTTPException(
        status_code=404,
        detail=f"Task with id {task_id} not found"
    )
