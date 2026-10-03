"""A story's page (BUILD_PLAN §13): one narrative the model grouped a brand's negative mentions
into, with its mentions, labelled AI-generated. The owner's only; anything else is a 404."""

import uuid

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from serpsense.entrypoints.web.pages import signed_in_page
from serpsense.entrypoints.web.session import container, current_user

router = APIRouter()


@router.get("/brands/{brand_id}/narratives/{narrative_id}")
def story(request: Request, brand_id: str, narrative_id: str) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status.HTTP_303_SEE_OTHER)
    try:
        brand, narrative = uuid.UUID(brand_id), uuid.UUID(narrative_id)
    except ValueError as exc:  # not ids at all: as missing as any other
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
    found = container(request).stories.story(user.user_id, brand, narrative)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return signed_in_page(request, user, "story.html", story=found)
