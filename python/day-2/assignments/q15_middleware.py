#Q15. Request Logging Middleware 
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
from enum import Enum
import logging
import time
from datetime import datetime

app = FastAPI()


# ─── LOGGING SETUP ────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(message)s",           # only message, no extra prefix
    handlers= [
        logging.FileHandler("api_logs.txt"),   # writes to file ✅
        logging.StreamHandler()                # also shows in terminal ✅
    ]
)
logger = logging.getLogger(__name__)


# ─── MIDDLEWARE ───────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):

    # BEFORE request
    start     = time.time()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    method    = request.method          # GET, POST, PUT etc.
    path      = request.url.path        # /tasks, /health etc.

    # call actual route function
    response  = await call_next(request)

    # AFTER response
    duration    = (time.time() - start) * 1000   # seconds → milliseconds
    status_code = response.status_code

    # build log entry
    log_entry = (
        f"{timestamp} | "
        f"{method} {path} | "
        f"Status: {status_code} | "
        f"Time: {duration:.0f}ms"
    )

    # log based on status code
    if status_code >= 500:
        logger.error(log_entry)        # 5xx → error ❌
    elif status_code >= 400:
        logger.warning(log_entry)      # 4xx → warning ⚠️
    else:
        logger.info(log_entry)         # 2xx → info ✅

    return response


# ─── CUSTOM EXCEPTIONS ────────────────────────────────
class TaskNotFoundError(Exception):
    def __init__(self, task_id: int):
        self.task_id     = task_id
        self.message     = f"Task with id {task_id} not found"
        self.status_code = 404


class TaskAlreadyExistsError(Exception):
    def __init__(self, title: str):
        self.title       = title
        self.message     = f"Task with title '{title}' already exists"
        self.status_code = 409


# ─── EXCEPTION HANDLERS ───────────────────────────────
@app.exception_handler(TaskNotFoundError)
async def task_not_found_handler(request: Request, exc: TaskNotFoundError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error"      : "TaskNotFoundError",
            "message"    : exc.message,
            "status_code": exc.status_code
        }
    )


@app.exception_handler(TaskAlreadyExistsError)
async def task_exists_handler(request: Request, exc: TaskAlreadyExistsError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error"      : "TaskAlreadyExistsError",
            "message"    : exc.message,
            "status_code": exc.status_code
        }
    )


# ─── ENUM ─────────────────────────────────────────────
class TaskStatus(str, Enum):
    pending     = "pending"
    in_progress = "in_progress"
    completed   = "completed"


# ─── PYDANTIC MODELS ──────────────────────────────────
class TaskCreate(BaseModel):
    title      : str
    description: str
    status     : TaskStatus = TaskStatus.pending


class TaskUpdate(BaseModel):
    title      : Optional[str]         = None
    description: Optional[str]         = None
    status     : Optional[TaskStatus]  = None


class TaskResponse(BaseModel):
    id         : int
    title      : str
    description: str
    status     : TaskStatus


# ─── STORAGE ──────────────────────────────────────────
tasks           = []
task_id_counter = 1


# ─── HELPER ───────────────────────────────────────────
def find_task(task_id: int):
    for task in tasks:
        if task["id"] == task_id:
            return task
    raise TaskNotFoundError(task_id)


# ─── HEALTH CHECK ─────────────────────────────────────
@app.get("/health")
def health_check():
    return {"status": "healthy"}


# ─── CREATE TASK ──────────────────────────────────────
@app.post("/tasks",
          response_model=TaskResponse,
          status_code=status.HTTP_201_CREATED)
def create_task(task: TaskCreate):
    global task_id_counter

    for t in tasks:
        if t["title"] == task.title:
            raise TaskAlreadyExistsError(task.title)

    new_task = {
        "id"         : task_id_counter,
        "title"      : task.title,
        "description": task.description,
        "status"     : task.status
    }
    tasks.append(new_task)
    task_id_counter += 1
    return new_task


# ─── LIST TASKS ───────────────────────────────────────
@app.get("/tasks", response_model=List[TaskResponse])
def get_tasks(status: Optional[TaskStatus] = None):
    if status:
        return [t for t in tasks if t["status"] == status]
    return tasks


# ─── GET SINGLE TASK ──────────────────────────────────
@app.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: int):
    return find_task(task_id)


# ─── UPDATE TASK ──────────────────────────────────────
@app.put("/tasks/{task_id}", response_model=TaskResponse)
def update_task(task_id: int, updated: TaskUpdate):
    task = find_task(task_id)

    if updated.title       is not None: task["title"]       = updated.title
    if updated.description is not None: task["description"] = updated.description
    if updated.status      is not None: task["status"]      = updated.status

    return task


# ─── DELETE TASK ──────────────────────────────────────
@app.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    task = find_task(task_id)
    tasks.remove(task)
    return {"message": f"Task {task_id} deleted successfully"}