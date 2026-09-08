"""Response schema for GET /api/categories -- see app/api/categories.py."""

from pydantic import BaseModel


class CategoryListResponse(BaseModel):
    categories: list[str]
