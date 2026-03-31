import os
import sys
from logging.config import fileConfig
from configparser import ConfigParser  # ✅ ADD THIS

from alembic import context
from sqlalchemy import engine_from_config, pool, text

# Ensure project root is on path so models can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from database import Base

# Import models so Alembic detects them for autogenerate
import models.db_models  # noqa: F401

config = context.config

# 🔥 ADD THIS LINE (VERY IMPORTANT FIX)
config.file_config = ConfigParser(interpolation=None)

# Override sqlalchemy.url from settings (reads .env)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

DB_SCHEMA = settings.DB_SCHEMA


def include_object(object, name, type_, reflected, compare_to):
    """Only track objects in the loanhub schema; ignore public schema noise."""
    if type_ == "table":
        return object.schema == DB_SCHEMA
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
        version_table_schema=DB_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Ensure the schema exists before running migrations
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            version_table_schema=DB_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()