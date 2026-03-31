import os
import re
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError, ArgumentError
from dotenv import load_dotenv

load_dotenv()                                       # load .env into environment

# ─────────────────────────────────────────────────
# Helper — mask password in URL for safe printing
# ─────────────────────────────────────────────────
def mask_password(url: str) -> str:
    """Replaces password in DB URL with *** for safe printing."""
    return re.sub(r"(:\/\/)([^:]+):([^@]+)(@)", r"\1\2:***\4", url)
    # postgresql://postgres:secret@host  →  postgresql://postgres:***@host


# ─────────────────────────────────────────────────
# Step 1 — Load DATABASE_URL
# ─────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError(
        "DATABASE_URL not found in .env file.\n"
        "Add: DATABASE_URL=postgresql://user:pass@host:5432/dbname"
    )


# ─────────────────────────────────────────────────
# Step 2 — Create Engine
# ─────────────────────────────────────────────────
engine = create_engine(
    DATABASE_URL,
    echo=False,                                     # no SQL logging per constraint
    pool_pre_ping=True,                             # test connection before use
)

print(f"Engine created: {mask_password(DATABASE_URL)}")


# ─────────────────────────────────────────────────
# Step 3 — Create Session Factory
# ─────────────────────────────────────────────────
SessionLocal = sessionmaker(
    autocommit=False,                               # manual commit control
    autoflush=False,                                # manual flush control
    bind=engine,                                    # tied to our engine
)

print("Session factory ready.")


# ─────────────────────────────────────────────────
# Step 4 — Declarative Base
# ─────────────────────────────────────────────────
Base = declarative_base()                           # all models will inherit this


# ─────────────────────────────────────────────────
# Step 5 — Verify Connection
# ─────────────────────────────────────────────────
def verify_connection():
    """Executes SELECT 1 to confirm database connection works."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))     # raw SQL via text()
            value  = result.scalar()                    # gets single value → 1
            print(f"Connection verified: SELECT 1 returned {value}")
            print("Database connection successful!")

    except OperationalError as e:
        print(f"Connection failed — cannot reach database: {e}")
        print("Check: host, port, password, database name in .env")
        raise

    except ArgumentError as e:
        print(f"Invalid DATABASE_URL format: {e}")
        raise

