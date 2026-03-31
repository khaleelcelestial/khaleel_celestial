from abc import ABC, abstractmethod
from typing import Any, Optional


class BaseRepository(ABC):
    """Abstract base repository — defines only CRUD methods (ISP-compliant)."""

    @abstractmethod
    def save(self, entity: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def find(self, id: int) -> Optional[Any]:
        raise NotImplementedError

    @abstractmethod
    def find_all(self, **filters) -> list[Any]:
        raise NotImplementedError

    @abstractmethod
    def update(self, entity: Any, **fields) -> Any:
        raise NotImplementedError

    @abstractmethod
    def delete(self, entity: Any) -> None:
        raise NotImplementedError
