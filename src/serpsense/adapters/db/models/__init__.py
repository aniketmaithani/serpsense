"""ORM models. Importing this package registers every table on `Base.metadata`."""

from serpsense.adapters.db.models.alerts import Alert, Notification, NotificationRead
from serpsense.adapters.db.models.audit import AuditEvent, AuditEventNetwork
from serpsense.adapters.db.models.brands import (
    Brand,
    BrandAlias,
    BrandApp,
    BrandCompetitor,
    BrandLanguage,
    BrandLocation,
    BrandWatchTerm,
)
from serpsense.adapters.db.models.identity import (
    OtpCode,
    OtpVerifyAttempt,
    User,
    UserLlmBudget,
    UserSearchBudget,
    UserSession,
)
from serpsense.adapters.db.models.llm import Enrichment, LlmCall, Narrative, NarrativeAssignment
from serpsense.adapters.db.models.mentions import Mention, MentionRevision
from serpsense.adapters.db.models.observations import (
    AppRatingObservation,
    MentionObservation,
    TrendsObservation,
)
from serpsense.adapters.db.models.outbox import OutboxAttempt, OutboxMessage
from serpsense.adapters.db.models.scans import Scan, ScanStatusTransition, ScanSurfaceResult
from serpsense.adapters.db.models.scores import (
    CrisisComponentValue,
    CrisisLevelThreshold,
    ScoreRun,
    ScoringVersion,
    ScoringWeight,
    SurfaceScore,
)
from serpsense.adapters.db.models.search import RawResponse, SerpCall
from serpsense.adapters.db.models.settings import (
    BrandScheduleVersion,
    BrandSearchSettingsVersion,
    UserLlmProfileVersion,
    UserSearchDefaultVersion,
)

__all__ = [
    "Alert",
    "AppRatingObservation",
    "AuditEvent",
    "AuditEventNetwork",
    "Brand",
    "BrandAlias",
    "BrandApp",
    "BrandCompetitor",
    "BrandLanguage",
    "BrandLocation",
    "BrandScheduleVersion",
    "BrandSearchSettingsVersion",
    "BrandWatchTerm",
    "CrisisComponentValue",
    "CrisisLevelThreshold",
    "Enrichment",
    "LlmCall",
    "Mention",
    "MentionObservation",
    "MentionRevision",
    "Narrative",
    "NarrativeAssignment",
    "Notification",
    "NotificationRead",
    "OtpCode",
    "OtpVerifyAttempt",
    "OutboxAttempt",
    "OutboxMessage",
    "RawResponse",
    "Scan",
    "ScanStatusTransition",
    "ScanSurfaceResult",
    "ScoreRun",
    "ScoringVersion",
    "ScoringWeight",
    "SerpCall",
    "SurfaceScore",
    "TrendsObservation",
    "User",
    "UserLlmBudget",
    "UserLlmProfileVersion",
    "UserSearchBudget",
    "UserSearchDefaultVersion",
    "UserSession",
]
