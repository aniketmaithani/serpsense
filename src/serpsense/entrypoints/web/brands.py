"""The brands list, a brand's page and its "Scan now" (BUILD_PLAN §13), for the signed-in owner
only; another user's brand is a 404, the same as one that doesn't exist (or an id that isn't
one)."""

import uuid
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from serpsense.entrypoints.web.pages import signed_in_page
from serpsense.entrypoints.web.session import container, current_user, require_csrf
from serpsense.services.scan_now import Requested

router = APIRouter()
SEE_OTHER = status.HTTP_303_SEE_OTHER
SAID: Mapping[str, str] = {  # what the page says after a "Scan now", by its outcome
    Requested.QUEUED: "Scan queued: new results in a few minutes.",
    Requested.BUSY: "A scan of this brand is already queued or running.",
    Requested.TOO_SOON: "This brand was scanned in the last 15 minutes; try again shortly.",
    Requested.OVER_BUDGET: "Your searches left this month can't cover another scan.",
    Requested.INVALID: "This brand's search settings can't be used for a scan.",
}


@router.get("/")
def home(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    cards = container(request).overview.brands(user.user_id)
    return signed_in_page(request, user, "home.html", cards=cards)


@router.get("/brands/{brand_id}")
def brand(request: Request, brand_id: str, scan: str = "") -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    wanted = _brand_id(brand_id)
    found = None if wanted is None else container(request).overview.brand(user.user_id, wanted)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    trend = [{"at": p.at.isoformat(), "health": p.health, "crisis": p.crisis} for p in found.trend]
    notice = SAID.get(scan)
    stories = container(request).stories.of_brand(user.user_id, found.card.brand_id, limit=5)
    context = {"brand": found, "trend": trend, "notice": notice, "stories": stories}
    return signed_in_page(request, user, "brand.html", **context)


@router.post("/brands/{brand_id}/scan")
def scan_now(request: Request, brand_id: str, csrf_token: Annotated[str, Form()] = "") -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    require_csrf(request, user, csrf_token)
    wanted = _brand_id(brand_id)
    asked = Requested.MISSING
    if wanted is not None:
        asked = container(request).scan_now.request(user.user_id, wanted)
    if asked is Requested.MISSING:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return RedirectResponse(f"/brands/{wanted}?scan={asked.value}", SEE_OTHER)


def _brand_id(text: str) -> uuid.UUID | None:
    """The id in the path; None for one that isn't an id at all, as missing as any other."""
    try:
        return uuid.UUID(text)
    except ValueError:
        return None
