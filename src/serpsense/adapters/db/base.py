"""SQLAlchemy declarative base with the project's constraint naming convention."""

from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(column_0_N_label)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


# Every timestamp column is timezone-aware and stored in UTC (AGENTS.md §3).
TIMESTAMPTZ = DateTime(timezone=True)


def pg_enum(enum: type[StrEnum], name: str) -> Enum:
    """A Postgres enum that stores the StrEnum's values (not its member names)."""
    return Enum(enum, name=name, values_callable=lambda members: [m.value for m in members])


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
