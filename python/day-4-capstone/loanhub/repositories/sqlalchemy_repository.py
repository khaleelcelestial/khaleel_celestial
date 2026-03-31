from typing import Any, Optional

from sqlalchemy.orm import Session

from repositories.base_repository import BaseRepository


class SQLAlchemyRepository(BaseRepository):
    """
    Concrete SQLAlchemy implementation of BaseRepository.
    LSP: drop-in replacement for any BaseRepository subclass.
    """

    def __init__(self, model, db: Session):
        self._model = model
        self._db = db

    def save(self, entity: Any) -> Any:
        self._db.add(entity)
        self._db.commit()
        self._db.refresh(entity)
        return entity

    def find(self, id: int) -> Optional[Any]:
        return self._db.query(self._model).filter(self._model.id == id).first()

    def find_all(self, **filters) -> list[Any]:
        query = self._db.query(self._model)
        for attr, value in filters.items():
            if value is not None:
                query = query.filter(getattr(self._model, attr) == value)
        return query.all()

    def update(self, entity: Any, **fields) -> Any:
        for key, value in fields.items():
            setattr(entity, key, value)
        self._db.commit()
        self._db.refresh(entity)
        return entity

    def delete(self, entity: Any) -> None:
        self._db.delete(entity)
        self._db.commit()

    # ── Extra query helpers (not part of the CRUD contract) ──────────────────

    def find_by(self, **filters) -> Optional[Any]:
        query = self._db.query(self._model)
        for attr, value in filters.items():
            query = query.filter(getattr(self._model, attr) == value)
        return query.first()

    def find_all_filtered(self, filters: dict, page: int = 1, limit: int = 10,
                          sort_by: str = "id", order: str = "desc") -> list[Any]:
        query = self._db.query(self._model)
        for attr, value in filters.items():
            if value is not None:
                query = query.filter(getattr(self._model, attr) == value)
        col = getattr(self._model, sort_by, self._model.id)
        if order == "desc":
            query = query.order_by(col.desc())
        else:
            query = query.order_by(col.asc())
        return query.offset((page - 1) * limit).limit(limit).all()

    def count(self, **filters) -> int:
        query = self._db.query(self._model)
        for attr, value in filters.items():
            if value is not None:
                query = query.filter(getattr(self._model, attr) == value)
        return query.count()

    def all_no_filter(self) -> list[Any]:
        return self._db.query(self._model).all()
