import logging
import logging.config
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer

from config import settings
from database import SessionLocal, create_schema_if_not_exists, verify_db_connection
from decorators.retry import retry
from exceptions.custom_exceptions import register_exception_handlers
from middleware.logging_middleware import RequestLoggingMiddleware
from routers.admin_router import router as admin_router
from routers.analytics_router import router as analytics_router
from routers.auth_router import router as auth_router
from routers.loan_router import router as loan_router


# ─── Logging Setup ────────────────────────────────────────────────────────────

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s - %(levelname)s - %(name)s - %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
        "app_file": {
            "class": "logging.FileHandler",
            "filename": "logs/app.log",
            "formatter": "standard",
        },
        "notifications_file": {
            "class": "logging.FileHandler",
            "filename": "logs/notifications.log",
            "formatter": "standard",
        },
    },
    "loggers": {
        "": {"handlers": ["console", "app_file"], "level": settings.LOG_LEVEL},
        "app": {"handlers": ["console", "app_file"], "level": settings.LOG_LEVEL, "propagate": False},
        "notifications": {
            "handlers": ["notifications_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


# ─── DB Verification with @retry ─────────────────────────────────────────────

@retry(max_attempts=3)
def check_db_connection():
    verify_db_connection()


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME}...")

    # Verify DB
    try:
        check_db_connection()
    except Exception as exc:
        logger.error(f"Database connection failed after retries: {exc}")

    # Ensure the loanhub schema exists in Supabase
    try:
        create_schema_if_not_exists()
    except Exception as exc:
        logger.error(f"Schema creation failed: {exc}")

    # Seed admin user
    db = SessionLocal()
    try:
        from services.user_service import UserService
        UserService(db).seed_admin(
            username=settings.ADMIN_USERNAME,
            password=settings.ADMIN_PASSWORD,
            email=settings.ADMIN_EMAIL,
        )
    finally:
        db.close()

    yield
    logger.info(f"{settings.APP_NAME} shutting down.")


# ─── App Factory ──────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "## LoanHub — Loan Application & Management System\n\n"
        "### Authentication\n"
        "1. Call **POST /auth/login** with your credentials.\n"
        "2. Copy the `access_token` from the response.\n"
        "3. Click the **Authorize 🔒** button at the top of this page.\n"
        "4. Enter `Bearer <your_token>` and click **Authorize**.\n\n"
        "All protected endpoints will then send your token automatically.\n\n"
        "**Roles:** `user` — apply & view own loans | `admin` — review all loans & analytics."
    ),
    version="2.0.0",
    lifespan=lifespan,
    # Swagger UI security scheme — shows the 🔒 Authorize button
    swagger_ui_parameters={"persistAuthorization": True},
)

# ─── OpenAPI security scheme registration ────────────────────────────────────
# This makes every 🔒 endpoint in Swagger accept a Bearer token

from fastapi.openapi.utils import get_openapi


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema.setdefault("components", {})
    schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Paste your JWT token here (without the 'Bearer ' prefix — Swagger adds it automatically).",
        }
    }
    # Apply security globally so every endpoint shows the lock icon
    for path_data in schema.get("paths", {}).values():
        for operation in path_data.values():
            operation.setdefault("security", [{"BearerAuth": []}])
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi

# ─── Middleware ───────────────────────────────────────────────────────────────

app.add_middleware(RequestLoggingMiddleware)

# ─── Exception Handlers ───────────────────────────────────────────────────────

register_exception_handlers(app)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "ValidationError",
            "message": str(exc.errors()),
            "status_code": 422,
        },
    )


# ─── Routers ──────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(loan_router)
app.include_router(admin_router)
app.include_router(analytics_router)


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["Utility"], summary="API & database health check")
def health_check():
    """Executes SELECT 1 via raw SQL to confirm the DB connection is alive."""
    try:
        verify_db_connection()
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"
    return {
        "status": "ok",
        "database": db_status,
        "schema": settings.DB_SCHEMA,
        "app": settings.APP_NAME,
    }
