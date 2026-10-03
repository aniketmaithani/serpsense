"""Scoring reference data (docs/architecture/data-model.md §7). Append-only."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from serpsense.adapters.db.base import TIMESTAMPTZ, Base, pg_enum
from serpsense.domain.enums import CrisisComponent, CrisisLevel, ScoreKind, Surface

SURFACES = ", ".join(f"'{s.value}'" for s in Surface)
COMPONENTS = ", ".join(f"'{c.value}'" for c in CrisisComponent)


def _version_fk() -> Mapped[str]:
    return mapped_column(
        Text, ForeignKey("scoring_versions.version", ondelete="RESTRICT"), primary_key=True
    )


class ScoringVersion(Base):
    """A version of the scoring rules; its weights and thresholds never change."""

    __tablename__ = "scoring_versions"
    __table_args__ = (
        CheckConstraint("version ~ '^s[0-9]{1,3}$'", name="version_format"),
        CheckConstraint("warm_up_scans >= 0", name="warm_up_scans_non_negative"),
    )

    version: Mapped[str] = mapped_column(Text, primary_key=True)
    # Earlier scored scans a brand needs before its crisis has a level.
    warm_up_scans: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ)


class ScoringWeight(Base):
    __tablename__ = "scoring_weights"
    __table_args__ = (
        CheckConstraint(
            f"(kind = 'health' AND component IN ({SURFACES})) "
            f"OR (kind = 'crisis' AND component IN ({COMPONENTS}))",
            name="component_of_kind",
        ),
        CheckConstraint("weight_bp BETWEEN 1 AND 10000", name="weight_bp_range"),
    )

    version: Mapped[str] = _version_fk()
    kind: Mapped[ScoreKind] = mapped_column(pg_enum(ScoreKind, "score_kind"), primary_key=True)
    component: Mapped[str] = mapped_column(Text, primary_key=True)  # a surface, or a component
    weight_bp: Mapped[int] = mapped_column(SmallInteger)  # basis points


class CrisisLevelThreshold(Base):
    __tablename__ = "crisis_level_thresholds"
    __table_args__ = (
        CheckConstraint("min_score BETWEEN 0 AND 100", name="min_score_range"),
        # Two levels with one floor would make the level ambiguous.
        UniqueConstraint("version", "min_score"),
    )

    version: Mapped[str] = _version_fk()
    level: Mapped[CrisisLevel] = mapped_column(
        pg_enum(CrisisLevel, "crisis_level"), primary_key=True
    )
    min_score: Mapped[int] = mapped_column(SmallInteger)
