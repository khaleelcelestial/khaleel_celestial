from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
from models.enums import TaskStatus, TaskPriority


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "taskapi"}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    full_name = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    by_default = Column(Integer, default=0)

    tasks = relationship("Task", back_populates="owner", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = {"schema": "taskapi"}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(SAEnum(TaskStatus, name="taskstatus", schema="taskapi", create_type=True), nullable=False, default=TaskStatus.pending)
    priority = Column(SAEnum(TaskPriority, name="taskpriority", schema="taskapi", create_type=True), nullable=False, default=TaskPriority.medium)
    owner_id = Column(Integer, ForeignKey("taskapi.users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    owner = relationship("User", back_populates="tasks")