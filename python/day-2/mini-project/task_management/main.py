import logging
import logging.config
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from config import settings
from routers.task_router import router as task_router
from routers.user_router import router as user_router
from middleware.logging_middleware import LoggingMiddleware
from exceptions.custom_exceptions import (
    UserNotFoundError,
    TaskNotFoundError,
    DuplicateUserError,
    InvalidCredentialsError,
)

# ── Logging setup ─────────────────────────────────────────────────────────────

os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
        "file": {
            "class": "logging.FileHandler",
            "filename": settings.log_file,
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
logger = logging.getLogger("main")

# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
)

# Middleware (cross-cutting concerns — SRP)
app.add_middleware(LoggingMiddleware)

# Routers
app.include_router(user_router)
app.include_router(task_router)


# ── Global exception handlers ─────────────────────────────────────────────────

@app.exception_handler(UserNotFoundError)
async def user_not_found_handler(request: Request, exc: UserNotFoundError):
    return JSONResponse(
        status_code=404,
        content={"error": "UserNotFoundError", "message": str(exc), "status_code": 404},
    )


@app.exception_handler(TaskNotFoundError)
async def task_not_found_handler(request: Request, exc: TaskNotFoundError):
    return JSONResponse(
        status_code=404,
        content={"error": "TaskNotFoundError", "message": str(exc), "status_code": 404},
    )


@app.exception_handler(DuplicateUserError)
async def duplicate_user_handler(request: Request, exc: DuplicateUserError):
    return JSONResponse(
        status_code=409,
        content={"error": "DuplicateUserError", "message": str(exc), "status_code": 409},
    )


@app.exception_handler(InvalidCredentialsError)
async def invalid_credentials_handler(request: Request, exc: InvalidCredentialsError):
    return JSONResponse(
        status_code=401,
        content={"error": "InvalidCredentialsError", "message": str(exc), "status_code": 401},
    )


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "ValidationError", "message": str(exc), "status_code": 422},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "InternalServerError", "message": "An unexpected error occurred.", "status_code": 500},
    )


# ── Startup / health ──────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    logger.info("%s v%s started successfully", settings.app_name, settings.app_version)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}
