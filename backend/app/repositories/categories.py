"""The complete set of distinct offense categories for a source (see
app/api/categories.py). Unlike app/repositories/summary.py's
`category_breakdown`, this is not scoped to any date range or
neighborhood and has no `limit` -- it exists specifically to answer
"what are all the valid category values", independent of whatever is
currently in scope for a trend query, so it stays a stable reference
list for building a filter UI (see docs/product.md "Safety & Ethics
Constraints" naming conventions -- this module counts nothing and
implies no risk/danger ranking, unlike category_breakdown).
"""

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.offense import Offense


def distinct_categories(db: Session, *, source_id: Optional[uuid.UUID]) -> list[str]:
    stmt = select(Offense.source_category).distinct().where(Offense.source_category.isnot(None))
    if source_id is not None:
        stmt = stmt.where(Offense.source_id == source_id)
    stmt = stmt.order_by(Offense.source_category)
    return [row[0] for row in db.execute(stmt).all()]
