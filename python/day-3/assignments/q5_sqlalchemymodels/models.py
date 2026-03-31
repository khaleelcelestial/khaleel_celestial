from sqlalchemy import (
    Column, Integer, String, Text,
    DateTime, ForeignKey
)
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


# ─────────────────────────────────────────────────────────
# User Model
# ─────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    # ── Columns ───────────────────────────────────────────
    id         = Column(Integer,      primary_key=True, autoincrement=True)
    username   = Column(String(50),   nullable=False,   unique=True)
    email      = Column(String(100),  nullable=False,   unique=True)
    password   = Column(String(255),  nullable=False)
    created_at = Column(DateTime,     default=datetime.utcnow)

    # ── Relationship — one User → many Tasks ──────────────
    tasks      = relationship(
        "Task",
        back_populates = "owner",
        cascade        = "all, delete-orphan",  # delete user → delete tasks
    )

    # ── Repr — NO password ────────────────────────────────
    def __repr__(self):
        return (
            f"<User("
            f"id={self.id}, "
            f"username='{self.username}', "
            f"email='{self.email}'"
            f")>"
        )


# ─────────────────────────────────────────────────────────
# Task Model
# ─────────────────────────────────────────────────────────
class Task(Base):
    __tablename__ = "tasks"

    # ── Columns ───────────────────────────────────────────
    id          = Column(Integer,     primary_key=True, autoincrement=True)
    title       = Column(String(200), nullable=False)
    description = Column(Text,        nullable=True)
    status      = Column(String(50),  default="pending")
    priority    = Column(String(50),  default="medium")
    owner_id    = Column(Integer,     ForeignKey("users.id"), nullable=True)
    created_at  = Column(DateTime,    default=datetime.utcnow)
    updated_at  = Column(DateTime,    default=datetime.utcnow,
                                      onupdate=datetime.utcnow)

    # ── Relationship — many Tasks → one User ──────────────
    owner       = relationship(
        "User",
        back_populates = "tasks",
    )

    # ── Repr ──────────────────────────────────────────────
    def __repr__(self):
        return (
            f"<Task("
            f"id={self.id}, "
            f"title='{self.title}', "
            f"status='{self.status}', "
            f"priority='{self.priority}', "
            f"owner_id={self.owner_id}"
            f")>"
        )