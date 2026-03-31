import logging
import logging.config
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import get_settings
from database import engine, Base
from middleware.logging_middleware import LoggingMiddleware
from routers import task_router, user_router
from exceptions.custom_exceptions import (
    TaskNotFoundError,
    UserNotFoundError,
    DuplicateUserError,
)

settings = get_settings()

# ── Logging setup ─────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
        "file": {
            "class": "logging.FileHandler",
            "filename": "logs/app.log",
            "formatter": "standard",
            "encoding": "utf-8",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": settings.log_level,
    },
}
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger("task_management")


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up %s — connecting to database…", settings.app_name)
    # Create all tables that don't exist yet (idempotent; Alembic handles migrations)
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables verified.")
    yield
    logger.info("Shutting down %s.", settings.app_name)


# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(LoggingMiddleware)


# ── Exception handlers ────────────────────────────────────────────────────────
@app.exception_handler(TaskNotFoundError)
async def task_not_found_handler(request: Request, exc: TaskNotFoundError):
    return JSONResponse(
        status_code=404,
        content={"detail": str(exc)},
    )


@app.exception_handler(UserNotFoundError)
async def user_not_found_handler(request: Request, exc: UserNotFoundError):
    return JSONResponse(
        status_code=404,
        content={"detail": str(exc)},
    )


@app.exception_handler(DuplicateUserError)
async def duplicate_user_handler(request: Request, exc: DuplicateUserError):
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc)},
    )


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(task_router.router)
app.include_router(user_router.router)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "app": settings.app_name}
