from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Optional, List, Any, Dict

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """
    Abstract base repository defining the CRUD interface.
    All concrete repositories (JSON, SQLAlchemy, etc.) must implement these methods.
    Adheres to the Dependency Inversion Principle — services depend on this abstraction.
    """

    @abstractmethod
    def save(self, entity: T) -> T:
        """Persist a new entity and return it with any auto-generated fields."""
        ...

    @abstractmethod
    def find(self, entity_id: int) -> Optional[T]:
        """Return the entity with the given id, or None if not found."""
        ...

    @abstractmethod
    def find_all(self, filters: Optional[Dict[str, Any]] = None) -> List[T]:
        """Return all entities, optionally filtered by the given key-value pairs."""
        ...

    @abstractmethod
    def update(self, entity_id: int, data: Dict[str, Any]) -> Optional[T]:
        """Apply the given key-value updates to the entity and return the updated entity."""
        ...

    @abstractmethod
    def delete(self, entity_id: int) -> bool:
        """Delete the entity with the given id. Returns True if deleted, False if not found."""
        ...
