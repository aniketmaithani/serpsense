"""Server-rendered pages (ADR-0002): Jinja with autoescaping, plain CSS, no inline scripts."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).parent
STATIC = HERE / "static"
TEMPLATES = Environment(
    loader=FileSystemLoader(HERE / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
SHOWN_IN = ZoneInfo("Asia/Kolkata")  # the demo's audience; stored times stay UTC


LABELS = {  # where capitalising the value reads wrong
    "ai_overview": "AI Overview",
    "serp_result": "Search result",
    "youtube": "YouTube",
    "youtube_video": "YouTube video",
    "play": "Google Play",
    "maps": "Google Maps",
    "trends": "Google Trends",
}


def _words(value: object) -> str:
    """An enum value as words: `search_page` reads "Search page", `ai_overview` "AI Overview"."""
    text = str(value)
    return LABELS.get(text, text.replace("_", " ").capitalize())


def _when(value: datetime | None) -> str:
    return "never" if value is None else value.astimezone(SHOWN_IN).strftime("%d %b, %H:%M IST")


TEMPLATES.filters["words"] = _words
TEMPLATES.filters["when"] = _when


def page(
    request: Request, template: str, *, status_code: int = 200, **context: Any
) -> HTMLResponse:
    """A rendered page; never cached, since every page is personal or a sign-in form."""
    html = TEMPLATES.get_template(template).render(request=request, **context)
    return HTMLResponse(html, status_code=status_code, headers={"Cache-Control": "no-store"})
