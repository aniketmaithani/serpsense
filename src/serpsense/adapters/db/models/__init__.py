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

__all__ = [
    "Brand",
    "BrandAlias",
    "BrandApp",
    "BrandCompetitor",
    "BrandLanguage",
    "BrandLocation",
    "BrandWatchTerm",
    "OtpCode",
    "OtpVerifyAttempt",
    "User",
    "UserLlmBudget",
    "UserSearchBudget",
    "UserSession",
]
