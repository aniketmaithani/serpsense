"""A user's brands and what they're monitored with, in Postgres (data-model §2, §3)."""

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Connection, Table, select
from sqlalchemy import insert as plain_insert
from sqlalchemy.dialects.postgresql import insert

from serpsense.adapters.db.models.brands import Brand, BrandAlias, BrandApp, BrandCompetitor
from serpsense.adapters.db.models.settings import BrandScheduleVersion, BrandSearchSettingsVersion
from serpsense.domain.enums import AppStore
from serpsense.domain.schedule import Schedule
from serpsense.ports.brand_store import NewBrand

BRANDS, ALIASES = cast(Table, Brand.__table__), cast(Table, BrandAlias.__table__)
APPS, COMPETITORS = cast(Table, BrandApp.__table__), cast(Table, BrandCompetitor.__table__)
SCHEDULES = cast(Table, BrandScheduleVersion.__table__)
SETTINGS = cast(Table, BrandSearchSettingsVersion.__table__)
SCHEMA_VERSION = 1  # of the search settings document (domain/settings/search.py)


class SqlBrandStore:
    """Works on the caller's connection and never commits."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def brand(self, new: NewBrand) -> uuid.UUID:
        row = {"id": uuid.uuid4(), "owner_id": new.owner_id, "name": new.name}
        row |= {"slug": new.slug, "created_at": new.created_at}
        inserted = self._insert(BRANDS, row)
        if inserted:
            return cast(uuid.UUID, row["id"])
        found = BRANDS.c.owner_id == new.owner_id, BRANDS.c.slug == new.slug
        return cast(uuid.UUID, self._conn.execute(select(BRANDS.c.id).where(*found)).scalar_one())

    def add_alias(self, brand_id: uuid.UUID, alias: str) -> bool:
        return self._insert(ALIASES, {"id": uuid.uuid4(), "brand_id": brand_id, "alias": alias})

    def add_app(self, brand_id: uuid.UUID, store: AppStore, app_id: str) -> uuid.UUID:
        row = {"id": uuid.uuid4(), "brand_id": brand_id, "store": store, "app_id": app_id}
        if self._insert(APPS, row):
            return cast(uuid.UUID, row["id"])
        found = APPS.c.brand_id == brand_id, APPS.c.store == store, APPS.c.app_id == app_id
        return cast(uuid.UUID, self._conn.execute(select(APPS.c.id).where(*found)).scalar_one())

    def link_competitor(self, brand_id: uuid.UUID, competitor_id: uuid.UUID) -> bool:
        pair = {"brand_id": brand_id, "competitor_brand_id": competitor_id}
        return self._insert(COMPETITORS, pair)

    def set_schedule(self, brand_id: uuid.UUID, schedule: Schedule, *, at: datetime) -> bool:
        version = {
            "interval_minutes": schedule.interval_minutes,
            "timezone": schedule.timezone,
            "quiet_start": schedule.quiet_start,
            "quiet_end": schedule.quiet_end,
        }
        columns = [SCHEDULES.c[name] for name in version]
        if self._latest(SCHEDULES, brand_id, *columns) == tuple(version.values()):
            return False
        self._version(SCHEDULES, brand_id, at, version)
        return True

    def set_search_settings(
        self, brand_id: uuid.UUID, document: Mapping[str, Any], *, at: datetime
    ) -> bool:
        stored = _json(document)  # as JSONB gives it back: tuples are lists
        if self._latest(SETTINGS, brand_id, SETTINGS.c.document) == (stored,):
            return False
        version = {"document": stored, "schema_version": SCHEMA_VERSION}
        self._version(SETTINGS, brand_id, at, version)
        return True

    def _version(
        self, table: Table, brand_id: uuid.UUID, at: datetime, values: Mapping[str, Any]
    ) -> None:
        """A plain insert: a second version at the same instant raises rather than vanishing."""
        row = {"id": uuid.uuid4(), "brand_id": brand_id, "created_at": at, **values}
        self._conn.execute(plain_insert(table).values(**row))

    def _insert(self, table: Table, row: Mapping[str, Any]) -> bool:
        """Whether the row was written. RETURNING says so; psycopg's rowcount came back -1 for
        these inserts even when a row was written."""
        statement = insert(table).values(**row).on_conflict_do_nothing()
        returned = statement.returning(*table.primary_key.columns)
        return self._conn.execute(returned).first() is not None

    def _latest(self, table: Table, brand_id: uuid.UUID, *columns: Any) -> tuple[Any, ...] | None:
        query = (
            select(*columns)
            .where(table.c.brand_id == brand_id)
            .order_by(table.c.created_at.desc())
            .limit(1)
        )
        row = self._conn.execute(query).one_or_none()
        return None if row is None else tuple(row)


def _json(value: Any) -> Any:
    """A settings document as plain JSON values; anything JSON can't hold is refused."""
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"a settings document can't hold a {type(value).__name__}")
