"""ORM models. Importing this package registers every table on `Base.metadata`."""

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
from serpsense.adapters.db.models.scans import Scan, ScanStatusTransition, ScanSurfaceResult
from serpsense.adapters.db.models.search import RawResponse, SerpCall
from serpsense.adapters.db.models.settings import (
    BrandScheduleVersion,
    BrandSearchSettingsVersion,
    UserLlmProfileVersion,
    UserSearchDefaultVersion,
)

__all__ = [
    "Brand",
    "BrandAlias",
    "BrandApp",
    "BrandCompetitor",
    "BrandLanguage",
    "BrandLocation",
    "BrandScheduleVersion",
    "BrandSearchSettingsVersion",
    "BrandWatchTerm",
    "OtpCode",
    "OtpVerifyAttempt",
    "RawResponse",
    "Scan",
    "ScanStatusTransition",
    "ScanSurfaceResult",
    "SerpCall",
    "User",
    "UserLlmBudget",
    "UserLlmProfileVersion",
    "UserSearchBudget",
    "UserSearchDefaultVersion",
    "UserSession",
]
