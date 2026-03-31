import logging

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

logger = logging.getLogger(__name__)

DB_SCHEMA = settings.DB_SCHEMA


class Base(DeclarativeBase):
    pass


def _set_search_path(dbapi_conn, connection_record):
    """Set search_path to the loanhub schema on every new connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}, public")
    cursor.close()


engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.POOL_SIZE,
    max_overflow=settings.MAX_OVERFLOW,
    pool_pre_ping=True,
)

# Wire the search_path setter so every connection lands in the loanhub schema
event.listen(engine, "connect", _set_search_path)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Dependency that yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_db_connection():
    """Execute SELECT 1 to verify the database connection. Used in /health."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    logger.info("Database connection verified successfully.")


def create_schema_if_not_exists():
    """Create the loanhub schema in Supabase/Postgres if it does not exist."""
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}"))
        conn.commit()
    logger.info(f"Schema '{DB_SCHEMA}' is ready.")
