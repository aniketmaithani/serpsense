"""A story's page (BUILD_PLAN §13): one narrative the model grouped a brand's negative mentions
into, with its mentions and the response drafts a person may copy, all labelled AI-generated.
Drafting is a CSRF-checked form that runs in the request (ADR-0008: the model never sends
anything), offered only in live mode with an Anthropic key, and only at the presets the user's
draft model can take. A draft carrying a link or contact details is flagged for checking. The
owner's only; anything else is a 404."""

import uuid
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from serpsense.config import RunMode
from serpsense.domain.enums import DraftKind, DraftPreset
from serpsense.entrypoints.web.pages import signed_in_page
from serpsense.entrypoints.web.session import container, current_user, require_csrf
from serpsense.services.drafts import Outcome

router = APIRouter()
SEE_OTHER = status.HTTP_303_SEE_OTHER
SAID: Mapping[str, str] = {  # what the page says after a draft was asked for, by its outcome
    Outcome.DRAFTED: "Drafted: read it, edit it, and copy it if it helps.",
    Outcome.EMPTY: "This story has no mentions to respond to yet.",
    Outcome.FAILED: "The model couldn't draft this right now. Try again in a moment.",
    Outcome.UNCITED: "The draft didn't rest on this story's mentions, so it was dropped.",
    Outcome.BUSY: "A draft of yours is still being written. Wait for it, then ask again.",
    Outcome.LIMITED: "Today's High thinking and Max drafts are used up; Standard ones still work.",
    Outcome.UNSUPPORTED: "Your draft model can't think that hard: change it in AI settings.",
    "unavailable": "Drafting needs live mode with the model set up; this server isn't.",
}


@router.get("/brands/{brand_id}/narratives/{narrative_id}")
def story(request: Request, brand_id: str, narrative_id: str, draft: str = "") -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    brand, narrative = _ids(brand_id, narrative_id)
    found = container(request).stories.story(user.user_id, brand, narrative)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    drafter = container(request).drafts
    drafts = drafter.drafts(user.user_id, brand, narrative)
    context = {"story": found, "drafts": drafts, "notice": SAID.get(draft)}
    context |= {"kinds": list(DraftKind), "drafting": _drafting(request)}
    context |= {"presets": drafter.presets(user.user_id)}
    return signed_in_page(request, user, "story.html", **context)


class DraftForm(BaseModel):
    kind: DraftKind
    preset: DraftPreset
    csrf_token: str = ""


@router.post("/brands/{brand_id}/narratives/{narrative_id}/drafts")
def ask_for_a_draft(
    request: Request, brand_id: str, narrative_id: str, form: Annotated[DraftForm, Form()]
) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    require_csrf(request, user, form.csrf_token)
    brand, narrative = _ids(brand_id, narrative_id)
    page = f"/brands/{brand}/narratives/{narrative}"
    if not _drafting(request):
        return RedirectResponse(f"{page}?draft=unavailable", SEE_OTHER)
    drafter = container(request).drafts
    result = drafter.draft(user.user_id, brand, narrative, kind=form.kind, preset=form.preset)
    if result.outcome is Outcome.MISSING:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return RedirectResponse(f"{page}?draft={result.outcome.value}", SEE_OTHER)


def _drafting(request: Request) -> bool:
    """Whether a live model is configured to draft with: replay mode never drafts (ADR-0008)."""
    settings = container(request).settings
    return settings.serpsense_mode is RunMode.LIVE and settings.anthropic_api_key is not None


def _ids(brand_id: str, narrative_id: str) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        return uuid.UUID(brand_id), uuid.UUID(narrative_id)
    except ValueError as exc:  # not ids at all: as missing as any other
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
