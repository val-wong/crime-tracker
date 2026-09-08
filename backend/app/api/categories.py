"""Reference endpoint for the complete set of valid category values.

Exists so the frontend's category filter can offer a complete,
stable option list (see frontend/src/components/FiltersPanel.tsx)
without deriving it from /api/summary's category_breakdown, which is
scoped to whatever date range/neighborhood happens to be currently
selected and therefore can omit real, valid categories that just
aren't among the top ones in that particular window.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.constants import CHICAGO_SOURCE_KEY
from app.db.session import get_db
from app.repositories import sources as sources_repo
from app.repositories.categories import distinct_categories
from app.schemas.category import CategoryListResponse

router = APIRouter(prefix="/api/categories", tags=["categories"])


@router.get("", response_model=CategoryListResponse)
def categories(db: Session = Depends(get_db)) -> CategoryListResponse:
    source = sources_repo.get_by_key(db, CHICAGO_SOURCE_KEY)
    source_id = source.id if source is not None else None
    return CategoryListResponse(categories=distinct_categories(db, source_id=source_id))
