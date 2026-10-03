"""The brands list and a brand's page (BUILD_PLAN §13), for the signed-in owner only; another
user's brand is a 404, the same as one that doesn't exist (or an id that isn't one)."""

import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from serpsense.entrypoints.web.pages import page
from serpsense.entrypoints.web.session import container, current_user, sessions

router = APIRouter()


@router.get("/")
def home(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status.HTTP_303_SEE_OTHER)
    cards = container(request).overview.brands(user.user_id)
    return page(request, "home.html", cards=cards, csrf_token=sessions(request).csrf_token(user))


@router.get("/brands/{brand_id}")
def brand(request: Request, brand_id: str) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status.HTTP_303_SEE_OTHER)
    found = None
    try:
        wanted = uuid.UUID(brand_id)
    except ValueError:  # not an id at all: as missing as any other
        wanted = None
    if wanted is not None:
        found = container(request).overview.brand(user.user_id, wanted)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    trend = [{"at": p.at.isoformat(), "health": p.health, "crisis": p.crisis} for p in found.trend]
    token = sessions(request).csrf_token(user)
    return page(request, "brand.html", brand=found, trend=trend, csrf_token=token)
