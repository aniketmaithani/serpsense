"""Users' model settings on Postgres (`user_llm_profile_versions`, data-model §3): the latest
version wins, a save that changes nothing writes nothing, and a user who never saved one gets
the configured preset (#97). A stored task that no longer validates (a model since dropped)
falls back to the preset for that task alone, and is logged, so calls never fail on it."""

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Engine, Table, insert, select

from serpsense.adapters.db.models.settings import UserLlmProfileVersion
from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import LlmPreset, TaskSettings, preset_settings
from serpsense.domain.settings.llm import SCHEMA_VERSION, LlmProfile, from_stored
from serpsense.observability import get_logger

log = get_logger(__name__)
PROFILES = cast(Table, UserLlmProfileVersion.__table__)


class SqlLlmProfiles:
    def __init__(self, engine: Engine, fallback: LlmPreset) -> None:
        self._engine, self._fallback = engine, fallback

    def settings(self, user_id: uuid.UUID, task: LlmTask) -> TaskSettings:
        profile = self.profile(user_id)
        return preset_settings(self._fallback, task) if profile is None else profile.settings(task)

    def profile(self, user_id: uuid.UUID) -> LlmProfile | None:
        with self._engine.connect() as conn:
            document = conn.execute(_latest(user_id)).scalar_one_or_none()
        if document is None:
            return None
        profile, dropped = from_stored(document, self._fallback)
        if dropped:
            log.warning("llm_profile.invalid", user_id=str(user_id), dropped=list(dropped))
        return profile

    def save(self, user_id: uuid.UUID, profile: LlmProfile, *, at: datetime) -> bool:
        document = profile.model_dump(mode="json")
        with self._engine.begin() as conn:
            if conn.execute(_latest(user_id)).scalar_one_or_none() == document:
                return False
            row: dict[str, Any] = {"id": uuid.uuid4(), "user_id": user_id, "created_at": at}
            row |= {"document": document, "schema_version": SCHEMA_VERSION}
            conn.execute(insert(PROFILES).values(**row))
        return True


def _latest(user_id: uuid.UUID) -> Any:
    return (
        select(PROFILES.c.document)
        .where(PROFILES.c.user_id == user_id)
        .order_by(PROFILES.c.created_at.desc())
        .limit(1)
    )
