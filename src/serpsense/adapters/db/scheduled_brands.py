"""Brands on a schedule, with what their next scan needs, in one query (data-model §2, §3)."""

from datetime import datetime
from typing import cast

from sqlalchemy import Connection, RowMapping, Subquery, Table, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by, distinct_on

from serpsense.adapters.db.models.brands import Brand, BrandApp, BrandLanguage, BrandLocation
from serpsense.adapters.db.models.identity import User
from serpsense.adapters.db.models.scans import Scan
from serpsense.adapters.db.models.settings import (
    BrandScheduleVersion,
    BrandSearchSettingsVersion,
    UserSearchDefaultVersion,
)
from serpsense.domain.enums import ScanStatus, ScanTrigger
from serpsense.domain.estimator import BrandFacts
from serpsense.ports.scheduled_brands import ScheduledBrand

BRANDS, USERS, SCANS = (cast(Table, m.__table__) for m in (Brand, User, Scan))
APPS, LOCATIONS = cast(Table, BrandApp.__table__), cast(Table, BrandLocation.__table__)
LANGUAGES = cast(Table, BrandLanguage.__table__)
SCHEDULES = cast(Table, BrandScheduleVersion.__table__)
BRAND_DOCS = cast(Table, BrandSearchSettingsVersion.__table__)
USER_DOCS = cast(Table, UserSearchDefaultVersion.__table__)


class SqlScheduledBrands:
    """Reads on the caller's connection."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def scheduled_brands(self, as_of: datetime) -> list[ScheduledBrand]:
        schedule = _latest(SCHEDULES, "brand_id", as_of)
        brand_doc, user_doc = (
            _latest(BRAND_DOCS, "brand_id", as_of),
            _latest(USER_DOCS, "user_id", as_of),
        )
        languages, apps, places, scans = _languages(), _apps(), _places(), _covering_scans(as_of)
        query = (
            select(
                BRANDS.c.id,
                schedule.c.interval_minutes,
                schedule.c.timezone,
                schedule.c.quiet_start,
                schedule.c.quiet_end,
                user_doc.c.document.label("user_defaults"),
                brand_doc.c.document.label("brand_settings"),
                languages.c.codes,
                apps.c.apps,
                places.c.places,
                places.c.unresolved,
                scans.c.last_scan_at,
            )
            .join(USERS, USERS.c.id == BRANDS.c.owner_id)
            .join(schedule, schedule.c.brand_id == BRANDS.c.id)
            .outerjoin(user_doc, user_doc.c.user_id == USERS.c.id)
            .outerjoin(brand_doc, brand_doc.c.brand_id == BRANDS.c.id)
            .outerjoin(languages, languages.c.brand_id == BRANDS.c.id)
            .outerjoin(apps, apps.c.brand_id == BRANDS.c.id)
            .outerjoin(places, places.c.brand_id == BRANDS.c.id)
            .outerjoin(scans, scans.c.brand_id == BRANDS.c.id)
            .where(
                BRANDS.c.archived_at.is_(None),
                USERS.c.deleted_at.is_(None),
                schedule.c.interval_minutes.is_not(None),
            )
            .order_by(BRANDS.c.id)
        )
        return [_brand(row) for row in self._conn.execute(query).mappings()]


def _latest(table: Table, key: str, as_of: datetime) -> Subquery:
    """The latest version per key; `uq_<table>_<key>_created_at` makes it a single row."""
    return (
        select(table)
        .where(table.c.created_at <= as_of)
        .ext(distinct_on(table.c[key]))
        .order_by(table.c[key], table.c.created_at.desc())
        .subquery()
    )


def _languages() -> Subquery:
    codes = func.array_agg(aggregate_order_by(LANGUAGES.c.language_code, LANGUAGES.c.language_code))
    return (
        select(LANGUAGES.c.brand_id, codes.label("codes")).group_by(LANGUAGES.c.brand_id).subquery()
    )


def _apps() -> Subquery:
    return select(APPS.c.brand_id, func.count().label("apps")).group_by(APPS.c.brand_id).subquery()


def _places() -> Subquery:
    unresolved = func.count().filter(LOCATIONS.c.resolved_data_id.is_(None))
    return (
        select(LOCATIONS.c.brand_id, func.count().label("places"), unresolved.label("unresolved"))
        .group_by(LOCATIONS.c.brand_id)
        .subquery()
    )


def _covering_scans(as_of: datetime) -> Subquery:
    """A failed or skipped scan gave its slot no data, and a replay belongs to no slot."""
    latest = func.max(SCANS.c.created_at).label("last_scan_at")
    return (
        select(SCANS.c.brand_id, latest)
        .where(
            SCANS.c.created_at <= as_of,
            SCANS.c.trigger != ScanTrigger.REPLAY,
            SCANS.c.status.not_in([ScanStatus.FAILED, ScanStatus.SKIPPED]),
        )
        .group_by(SCANS.c.brand_id)
        .subquery()
    )


def _brand(row: RowMapping) -> ScheduledBrand:
    return ScheduledBrand(
        brand_id=row["id"],
        interval_minutes=row["interval_minutes"],
        timezone=row["timezone"],
        quiet_start=row["quiet_start"],
        quiet_end=row["quiet_end"],
        user_defaults=row["user_defaults"] or {},
        brand_settings=row["brand_settings"] or {},
        languages=tuple(row["codes"] or ()),
        facts=BrandFacts(
            apps=row["apps"] or 0,
            locations=row["places"] or 0,
            unresolved_locations=row["unresolved"] or 0,
        ),
        last_scan_at=row["last_scan_at"],
    )
