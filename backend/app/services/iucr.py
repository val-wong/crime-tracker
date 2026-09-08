"""Refresh the local IUCR reference table from Chicago's official
reference dataset (`c7ck-438e`) — see docs/sources/chicago.md §7.

Kept separate from reconciliation: this is a small (~434 row),
independent reference dataset, not part of the crimes dataset's own
source-record/versioning lifecycle.
"""

from sqlalchemy.orm import Session

from app.adapters.chicago import ChicagoSourceAdapter
from app.repositories import iucr_codes as iucr_codes_repo


def refresh_iucr_codes(db: Session, adapter: ChicagoSourceAdapter) -> int:
    """Fetch the current IUCR reference table and upsert it locally.

    Never deletes a code that has disappeared from the source's table
    — see app/models/iucr_code.py for why (a historical offense may
    legitimately reference a code no longer listed).
    """
    rows = adapter.fetch_iucr_codes()
    return iucr_codes_repo.upsert_many(db, rows)
