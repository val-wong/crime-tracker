"""Persistence functions for the IUCR reference table.

Bulk-oriented on purpose: a refresh replaces/updates all ~434 rows at
once (see app/services/iucr.py), not one row at a time.
"""

from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.mixins import utcnow
from app.models.iucr_code import IucrCode


def upsert_many(db: Session, rows: Sequence[dict[str, Any]]) -> int:
    """Insert new codes, update existing ones (by `iucr`) — never
    deletes a code that has disappeared from the source's reference
    table, since a historical offense may still legitimately reference
    it (see app/models/iucr_code.py).
    """
    if not rows:
        return 0
    now = utcnow()
    values = [
        {
            "iucr": row["iucr"],
            "primary_description": row["primary_description"],
            "secondary_description": row["secondary_description"],
            "index_code": row["index_code"],
            "active": row["active"],
            "last_refreshed_at": now,
        }
        for row in rows
    ]
    stmt = pg_insert(IucrCode.__table__).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["iucr"],
        set_={
            "primary_description": stmt.excluded.primary_description,
            "secondary_description": stmt.excluded.secondary_description,
            "index_code": stmt.excluded.index_code,
            "active": stmt.excluded.active,
            "last_refreshed_at": stmt.excluded.last_refreshed_at,
        },
    )
    db.execute(stmt)
    db.flush()
    return len(values)


def get_by_code(db: Session, iucr: str) -> Optional[IucrCode]:
    return db.execute(select(IucrCode).where(IucrCode.iucr == iucr)).scalar_one_or_none()


def count(db: Session) -> int:
    return len(db.execute(select(IucrCode.iucr)).scalars().all())
