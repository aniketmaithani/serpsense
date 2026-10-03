"""The public landing page at `/` for signed-out visitors (issue #183): one scroll-driven story of
what SerpSense does, told with a fictional brand (VoltBox) and competitor (SoundNest). The copy
and every chapter's end state are plain HTML, so the page reads without JavaScript and with
reduced motion; `static/landing/` animates it when motion is welcome."""

from fastapi import Request, Response

from serpsense.entrypoints.web.pages import page

SURFACES = (
    "Results",
    "Autocomplete",
    "AI Overview",
    "News",
    "Trends",
    "Play reviews",
    "Maps reviews",
)
# engine, the surface it reads, searches a scan makes for VoltBox (illustrative)
ENGINES = (
    ("google", "Results", 2),
    ("google_autocomplete", "Autocomplete", 3),
    ("google_ai_overview", "AI Overview", 1),
    ("google_news", "News", 2),
    ("google_trends", "Trends", 2),
    ("google_play_product", "Play reviews", 2),
    ("google_maps_reviews", "Maps reviews", 1),
)
# one mention per cell: positive, neutral or negative, as the enrich chapter tints them
TONES = "pnnpxnxpnnxxpnpxnnpxxnpn"
STORIES = (
    ("Battery swelling", 9, "Play reviews, News, Results"),
    ("Refund delays", 6, "Play reviews, Maps reviews"),
)
# docs/scoring.md: health weights in percent
WEIGHTS = (
    ("Results", 25),
    ("Autocomplete", 20),
    ("AI Overview", 15),
    ("News", 15),
    ("Play", 15),
    ("Maps", 10),
)
FACTS = (
    "Postgres as the source of truth",
    "Idempotent scans",
    "Transactional outbox",
    "Per-engine circuit breaker",
    "Budget and quota checks before every scan",
    "Golden-set evals",
    "Replay mode that needs no API keys",
)

RAIL = (
    ("hero", "Search"),
    ("surfaces", "Surfaces"),
    ("collect", "Collect"),
    ("normalise", "Normalise"),
    ("enrich", "Enrich"),
    ("narratives", "Stories"),
    ("score", "Score"),
    ("alert", "Alert"),
    ("draft", "Draft"),
    ("replay", "Replay"),
    ("built-right", "Built right"),
    ("cta", "Run it"),
)


def landing_page(request: Request) -> Response:
    return page(
        request,
        "landing.html",
        surfaces=SURFACES,
        engines=ENGINES,
        tones=TONES,
        stories=STORIES,
        weights=WEIGHTS,
        facts=FACTS,
        rail=RAIL,
    )
