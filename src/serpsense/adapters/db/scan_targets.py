"""The brand behind a scan, as its worker needs it (data-model §3, §4)."""

import uuid
from typing import cast

from sqlalchemy import Connection, Table, select

from serpsense.adapters.db.models.brands import Brand, BrandAlias, BrandApp, BrandCompetitor
from serpsense.adapters.db.models.identity import User
from serpsense.adapters.db.models.scans import Scan
from serpsense.domain.enums import AppStore
from serpsense.ports.scan_targets import NamedBrand, ScanTarget, StoreApp

SCANS, BRANDS, USERS = (cast(Table, m.__table__) for m in (Scan, Brand, User))
ALIASES, APPS = cast(Table, BrandAlias.__table__), cast(Table, BrandApp.__table__)
COMPETITORS = cast(Table, BrandCompetitor.__table__)


class SqlScanTargets:
    """Reads on the caller's connection."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def for_scan(self, scan_id: uuid.UUID) -> ScanTarget | None:
        query = (
            select(
                SCANS.c.settings_snapshot,
                BRANDS.c.id,
                BRANDS.c.name,
                BRANDS.c.owner_id,
                BRANDS.c.archived_at,
                USERS.c.deleted_at,
            )
            .join(BRANDS, BRANDS.c.id == SCANS.c.brand_id)
            .join(USERS, USERS.c.id == BRANDS.c.owner_id)
            .where(SCANS.c.id == scan_id)
        )
        row = self._conn.execute(query).one_or_none()
        if row is None:
            return None
        brand_id = row.id
        return ScanTarget(
            scan_id=scan_id,
            brand=NamedBrand(brand_id, row.name),
            owner_id=row.owner_id,
            settings_snapshot=dict(row.settings_snapshot),
            aliases=self._aliases(brand_id),
            competitors=self._competitors(brand_id),
            apps=self._apps(brand_id),
            brand_archived=row.archived_at is not None,
            owner_deleted=row.deleted_at is not None,
        )

    def _aliases(self, brand_id: uuid.UUID) -> tuple[str, ...]:
        query = select(ALIASES.c.alias).where(ALIASES.c.brand_id == brand_id)
        return tuple(self._conn.execute(query.order_by(ALIASES.c.alias)).scalars())

    def _competitors(self, brand_id: uuid.UUID) -> tuple[NamedBrand, ...]:
        query = (
            select(BRANDS.c.id, BRANDS.c.name)
            .join(COMPETITORS, COMPETITORS.c.competitor_brand_id == BRANDS.c.id)
            .where(COMPETITORS.c.brand_id == brand_id)
            .order_by(BRANDS.c.name, BRANDS.c.id)
        )
        return tuple(NamedBrand(row.id, row.name) for row in self._conn.execute(query))

    def _apps(self, brand_id: uuid.UUID) -> tuple[StoreApp, ...]:
        query = (
            select(APPS.c.id, APPS.c.app_id)
            .where(APPS.c.brand_id == brand_id, APPS.c.store == AppStore.GOOGLE_PLAY)
            .order_by(APPS.c.app_id)
        )
        return tuple(StoreApp(row.id, row.app_id) for row in self._conn.execute(query))
