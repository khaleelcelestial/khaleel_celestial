# ─── IMPORTS ──────────────────────────────────────────
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, List
from enum import Enum
import logging
import time
import json
import os
from datetime import datetime


# ─── SETTINGS CLASS ───────────────────────────────────
class Settings(BaseSettings):
    app_name    : str  = "DefaultApp"
    debug       : bool = False
    json_db_path: str  = "./tasks.json"
    log_level   : str  = "INFO"

    model_config = SettingsConfigDict(
        env_file    = ".env",
        extra       = "ignore"           # ← ignores extra .env fields ✅
    )


# ─── SINGLETON ────────────────────────────────────────
settings = Settings()


# ─── LOGGING SETUP ────────────────────────────────────
log_level_int = getattr(logging, settings.log_level.upper(), logging.INFO)
                                         # "INFO" → logging.INFO ✅

logging.basicConfig(
    level   = log_level_int,             # ← integer now ✅
    format  = "%(message)s",
    handlers= [
        logging.FileHandler("api_logs.txt"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ─── APP ──────────────────────────────────────────────
app = FastAPI(title=settings.app_name)


# ─── STARTUP EVENT ────────────────────────────────────
@app.on_event("startup")
def startup_event():
    print(f"App: {settings.app_name} | "
          f"Debug: {settings.debug} | "
          f"DB: {settings.json_db_path}")
    logger.info(f"Server started → {settings.app_name}")


# ─── MIDDLEWARE ───────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start       = time.time()
    timestamp   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    method      = request.method
    path        = request.url.path
    response    = await call_next(request)
    duration    = (time.time() - start) * 1000
    status_code = response.status_code

    log_entry = (
        f"{timestamp} | "
        f"{method} {path} | "
        f"Status: {status_code} | "
        f"Time: {duration:.0f}ms"
    )

    if status_code >= 500:   logger.error(log_entry)
    elif status_code >= 400: logger.warning(log_entry)
    else:                    logger.info(log_entry)

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
    title      : Optional[str]        = None
    description: Optional[str]        = None
    status     : Optional[TaskStatus] = None


class TaskResponse(BaseModel):
    id         : int
    title      : str
    description: str
    status     : TaskStatus


# ─── JSON FILE STORAGE HELPERS ────────────────────────
def load_tasks():
    if os.path.exists(settings.json_db_path):
        with open(settings.json_db_path, "r") as f:
            return json.load(f)
    return []


def save_tasks(data):
    folder = os.path.dirname(settings.json_db_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(settings.json_db_path, "w") as f:
        json.dump(data, f, indent=4)


# ─── STORAGE ──────────────────────────────────────────
tasks           = load_tasks()
task_id_counter = max(
    [t["id"] for t in tasks],
    default=0
) + 1


# ─── HELPER ───────────────────────────────────────────
def find_task(task_id: int):
    for task in tasks:
        if task["id"] == task_id:
            return task
    raise TaskNotFoundError(task_id)


# ─── HEALTH CHECK ─────────────────────────────────────
@app.get("/health")
def health_check():
    return {
        "status" : "healthy",
        "app"    : settings.app_name,
        "debug"  : settings.debug
    }


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
    save_tasks(tasks)
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

    save_tasks(tasks)
    return task


# ─── DELETE TASK ──────────────────────────────────────
@app.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    task = find_task(task_id)
    tasks.remove(task)
    save_tasks(tasks)
    return {"message": f"Task {task_id} deleted successfully"}