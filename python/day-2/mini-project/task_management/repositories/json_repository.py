import json
import logging
import os
from typing import Any, Dict, List, Optional

from filelock import FileLock

from repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class JSONRepository(BaseRepository):
    """
    Concrete repository that persists data to a JSON file.
    Uses a file lock for safe concurrent access and atomic writes.
    Implements BaseRepository so it is fully substitutable (LSP).
    """

    def __init__(self, file_path: str, collection_key: str) -> None:
        self._file_path = file_path
        self._collection_key = collection_key
        self._lock_path = file_path + ".lock"
        self._ensure_file()

    # ── private helpers ──────────────────────────────────────────────────────

    def _ensure_file(self) -> None:
        """Create the JSON file with an empty collection if it does not exist or is corrupted."""
        os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
        if not os.path.exists(self._file_path):
            self._write({self._collection_key: []})
            logger.info("Created new data file: %s", self._file_path)
            return
        try:
            with open(self._file_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if self._collection_key not in data:
                data[self._collection_key] = []
                self._write(data)
        except (json.JSONDecodeError, OSError):
            logger.error("Corrupted or unreadable file %s — recreating.", self._file_path)
            self._write({self._collection_key: []})

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self._file_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read %s: %s", self._file_path, exc)
            return {self._collection_key: []}

    def _write(self, data: Dict[str, Any]) -> None:
        """Atomic write: write to a temp file then replace."""
        tmp_path = self._file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        os.replace(tmp_path, self._file_path)

    def _next_id(self, records: List[Dict[str, Any]]) -> int:
        return max((r["id"] for r in records), default=0) + 1

    # ── BaseRepository implementation ────────────────────────────────────────

    def find_all(self) -> List[Dict[str, Any]]:
        with FileLock(self._lock_path):
            return self._read().get(self._collection_key, [])

    def find_by_id(self, record_id: int) -> Optional[Dict[str, Any]]:
        with FileLock(self._lock_path):
            records = self._read().get(self._collection_key, [])
            return next((r for r in records if r["id"] == record_id), None)

    def save(self, record: Dict[str, Any]) -> Dict[str, Any]:
        with FileLock(self._lock_path):
            data = self._read()
            records: List[Dict[str, Any]] = data.setdefault(self._collection_key, [])
            record["id"] = self._next_id(records)
            records.append(record)
            self._write(data)
            return record

    def update(self, record_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with FileLock(self._lock_path):
            file_data = self._read()
            records: List[Dict[str, Any]] = file_data.get(self._collection_key, [])
            for i, rec in enumerate(records):
                if rec["id"] == record_id:
                    records[i] = {**rec, **data, "id": record_id}
                    self._write(file_data)
                    return records[i]
            return None

    def delete(self, record_id: int) -> bool:
        with FileLock(self._lock_path):
            file_data = self._read()
            records: List[Dict[str, Any]] = file_data.get(self._collection_key, [])
            new_records = [r for r in records if r["id"] != record_id]
            if len(new_records) == len(records):
                return False
            file_data[self._collection_key] = new_records
            self._write(file_data)
            return True

    # ── Extra query helpers used by services ────────────────────────────────

    def find_by_field(self, field: str, value: Any) -> Optional[Dict[str, Any]]:
        with FileLock(self._lock_path):
            records = self._read().get(self._collection_key, [])
            return next((r for r in records if r.get(field) == value), None)
