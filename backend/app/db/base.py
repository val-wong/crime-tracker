"""Shared SQLAlchemy declarative base.

All ORM models import ``Base`` from here so that a single
``Base.metadata`` exists for Alembic autogeneration/reflection and for
test setup to create/drop the full schema.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
