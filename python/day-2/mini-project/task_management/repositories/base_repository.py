from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseRepository(ABC):
    """
    Abstract base repository defining the data-access contract.
    Services depend on this abstraction (DIP), not on concrete implementations.
    Any concrete class (JSON, SQL, etc.) can substitute this (LSP).
    Interface is limited to data-access concerns only (ISP).
    """

    @abstractmethod
    def find_all(self) -> List[Dict[str, Any]]:
        """Return all records."""

    @abstractmethod
    def find_by_id(self, record_id: int) -> Optional[Dict[str, Any]]:
        """Return a single record by its id, or None."""

    @abstractmethod
    def save(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a new record and return it (with assigned id)."""

    @abstractmethod
    def update(self, record_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an existing record and return the updated version, or None."""

    @abstractmethod
    def delete(self, record_id: int) -> bool:
        """Delete a record by id. Return True on success, False if not found."""
