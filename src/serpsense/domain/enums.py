"""Enumerations shared across layers (mirrored by Postgres enums where stored)."""

from enum import StrEnum


class AppStore(StrEnum):
    """Stores whose app reviews SerpSense collects (`app_store` Postgres enum)."""

    GOOGLE_PLAY = "google_play"


class ScanTrigger(StrEnum):
    """Why a scan exists (`scan_trigger` Postgres enum)."""

    SCHEDULE = "schedule"
    MANUAL = "manual"
    REPLAY = "replay"


class ScanStatus(StrEnum):
    """Scan lifecycle state (`scan_status` Postgres enum); transitions live in domain.scan_state."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class TransitionActor(StrEnum):
    """Who changed a scan's status (`transition_actor` Postgres enum)."""

    SYSTEM = "system"
    USER = "user"


class Surface(StrEnum):
    """A search surface a scan collects (`surface` Postgres enum)."""

    SEARCH_PAGE = "search_page"
    AI_OVERVIEW = "ai_overview"
    AUTOCOMPLETE = "autocomplete"
    NEWS = "news"
    TRENDS = "trends"
    PLAY = "play"
    MAPS = "maps"
    YOUTUBE = "youtube"


class SurfaceOutcome(StrEnum):
    """How collecting one surface went (`surface_outcome` Postgres enum)."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DISABLED = "disabled"
    NOT_SHOWN = "not_shown"
    CIRCUIT_OPEN = "circuit_open"
    BUDGET_EXHAUSTED = "budget_exhausted"


class SerpEngine(StrEnum):
    """SerpApi engines SerpSense calls (`serp_engine` Postgres enum); values are engine ids."""

    GOOGLE = "google"
    GOOGLE_AI_OVERVIEW = "google_ai_overview"
    GOOGLE_AUTOCOMPLETE = "google_autocomplete"
    GOOGLE_NEWS = "google_news"
    GOOGLE_TRENDS = "google_trends"
    GOOGLE_PLAY_PRODUCT = "google_play_product"
    GOOGLE_MAPS = "google_maps"
    GOOGLE_MAPS_REVIEWS = "google_maps_reviews"
    YOUTUBE = "youtube"


class ServedFrom(StrEnum):
    """Where a SerpApi result came from (`served_from`); only `live` is billable."""

    LOCAL_CACHE = "local_cache"
    SERPAPI_CACHE = "serpapi_cache"
    LIVE = "live"


class SerpCallOutcome(StrEnum):
    """How a SerpApi call went (`serp_call_outcome`); skipped calls never reached SerpApi."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED_BUDGET = "skipped_budget"
    CIRCUIT_OPEN = "circuit_open"


class SerpErrorCode(StrEnum):
    """Why a SerpApi call failed (`serp_calls.error_code`). Only transient failures are retried
    and counted by the circuit breaker (data-model §5)."""

    NETWORK = "serpapi.network"
    TIMEOUT = "serpapi.timeout"
    HTTP_429 = "serpapi.http_429"
    HTTP_5XX = "serpapi.http_5xx"
    HTTP_4XX = "serpapi.http_4xx"  # the exact status is in `http_status`
    SEARCH_ERROR = "serpapi.search_error"
    INVALID_RESPONSE = "serpapi.invalid_response"

    @property
    def is_transient(self) -> bool:
        return self in {self.NETWORK, self.TIMEOUT, self.HTTP_429, self.HTTP_5XX}


class MentionSource(StrEnum):
    """Where a mention was found (`mention_source` Postgres enum)."""

    SERP_RESULT = "serp_result"
    TOP_STORY = "top_story"
    PEOPLE_ALSO_ASK = "people_also_ask"
    AUTOCOMPLETE = "autocomplete"
    AI_OVERVIEW = "ai_overview"
    NEWS = "news"
    TRENDS_QUERY = "trends_query"
    PLAY_REVIEW = "play_review"
    MAPS_REVIEW = "maps_review"
    YOUTUBE_VIDEO = "youtube_video"


class LlmTask(StrEnum):
    """What a model call is for (`llm_task` Postgres enum; BUILD_PLAN §7.1)."""

    LABEL_MENTIONS = "label_mentions"
    CLASSIFY_AUTOCOMPLETE = "classify_autocomplete"
    ASSESS_AI_OVERVIEW = "assess_ai_overview"
    GROUP_NARRATIVES = "group_narratives"
    EXPLAIN_CRISIS = "explain_crisis"
    DRAFT_RESPONSE = "draft_response"


class LlmCallOutcome(StrEnum):
    """How a model call ended (`llm_call_outcome` Postgres enum)."""

    SUCCEEDED = "succeeded"
    REFUSED = "refused"  # stop_reason "refusal"
    TRUNCATED = "truncated"  # stop_reason "max_tokens"
    INVALID_OUTPUT = "invalid_output"  # the structured output failed validation
    FAILED = "failed"  # no response: network, timeout or an API error


class Topic(StrEnum):
    """What a mention is about (`topic` Postgres enum): the taxonomy the labelling prompt uses."""

    PRODUCT_QUALITY = "product_quality"
    PRICING = "pricing"
    BILLING_REFUNDS = "billing_refunds"
    CUSTOMER_SERVICE = "customer_service"
    RELIABILITY = "reliability"  # availability, delays, cancellations
    SAFETY = "safety"
    APP_EXPERIENCE = "app_experience"
    PRIVACY_SECURITY = "privacy_security"
    FRAUD_SCAM = "fraud_scam"  # fake support numbers, phishing in the brand's name
    MARKETING_ETHICS = "marketing_ethics"  # campaigns, boycotts, conduct
    LEGAL_REGULATORY = "legal_regulatory"
    CORPORATE = "corporate"  # leadership, finances, stock, funding
    WORKFORCE = "workforce"  # employees, drivers, partners, layoffs
    OTHER = "other"


class ScoreKind(StrEnum):
    """Which score a weight belongs to (`score_kind` Postgres enum)."""

    HEALTH = "health"
    CRISIS = "crisis"


class CrisisComponent(StrEnum):
    """What a crisis score is made of (`crisis_component` Postgres enum; docs/scoring.md)."""

    VELOCITY = "velocity"  # negative mentions growing against the brand's usual
    SPREAD = "spread"  # negativity on many surfaces at once
    AUTOCOMPLETE = "autocomplete"  # a new negative suggestion as people type the name
    TRENDS = "trends"  # a rising negative related query
    PRESS = "press"  # negative news in the last 48 hours


class CrisisLevel(StrEnum):
    """How bad it is (`crisis_level` Postgres enum). Compare levels by `rank`: as strings,
    "low" would sort after "high"."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return list(CrisisLevel).index(self)


class AlertRule(StrEnum):
    """Why an alert fired (`alert_rule` Postgres enum; BUILD_PLAN §12)."""

    LEVEL_INCREASE = "level_increase"  # the crisis level went up
    NEW_NEGATIVE_AUTOCOMPLETE = "new_negative_autocomplete"  # people see it as they type
    NARRATIVE_SPREAD = "narrative_spread"  # a story reached 5+ mentions on 2+ surfaces


class OutboxKind(StrEnum):
    """What an outbox email is (`outbox_kind` Postgres enum; ADR-0010)."""

    OTP_EMAIL = "otp_email"
    ALERT_EMAIL = "alert_email"


class OutboxStatus(StrEnum):
    """Where an outbox email stands (`outbox_status`); derived from its attempts (data-model §8)."""

    PENDING = "pending"
    SENT = "sent"
    DEAD = "dead"  # a permanent error, or 8 retryable ones
    DROPPED = "dropped"  # not sent on purpose: an expired code, a deleted account


class OutboxOutcome(StrEnum):
    """How one send attempt went (`outbox_attempt_outcome`)."""

    SENT = "sent"
    RETRYABLE_ERROR = "retryable_error"
    PERMANENT_ERROR = "permanent_error"
    DROPPED = "dropped"


class AuditAction(StrEnum):
    """What an audit event records (`audit_events.action`, noun.verb_past; ADR-0009, 0013)."""

    CODE_REQUESTED = "auth.code_requested"
    LOGIN_SUCCEEDED = "auth.login_succeeded"
    VERIFY_FAILED = "auth.verify_failed"
    LOGGED_OUT = "auth.logged_out"
    SESSIONS_REVOKED = "auth.sessions_revoked"
    ACCOUNT_DELETED = "account.deleted"


class AuditTarget(StrEnum):
    """What an audit event is about (`audit_events.target_type`); deletion finds a user's
    pre-login events through `otp_code`."""

    OTP_CODE = "otp_code"
    USER = "user"
    SESSION = "session"
