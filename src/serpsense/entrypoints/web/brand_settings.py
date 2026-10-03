"""A brand's search settings page (BUILD_PLAN §6, §13): the knobs a scan uses, its languages and
schedule, a preset to start from, and the searches it would cost, previewed before saving, one
section at a time (`settings_form`). The owner's only; anyone else's brand is a 404. Every write
carries the session's CSRF token."""

import uuid
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from serpsense.domain.schedule import INTERVALS_MINUTES, InvalidSchedule
from serpsense.domain.settings.search import Preset, ReviewSort
from serpsense.entrypoints.web.pages import signed_in_page
from serpsense.entrypoints.web.session import container, current_user, require_csrf
from serpsense.entrypoints.web.settings_form import (
    RANGES,
    SECTIONS,
    Section,
    SettingsForm,
    hidden,
    section_of,
)
from serpsense.services.brand_settings import Saved, SettingsView
from serpsense.services.sessions import CurrentUser

router = APIRouter()
SEE_OTHER = status.HTTP_303_SEE_OTHER
SAID: Mapping[str, str] = {
    Saved.SAVED: "Saved: the next scan uses these settings.",
    Saved.UNCHANGED: "Nothing changed.",
    Saved.INVALID: "Those settings can't be used for a scan.",
}


@router.get("/brands/{brand_id}/settings")
def settings_page(request: Request, brand_id: str, saved: str = "", section: str = "") -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    wanted = _brand_id(brand_id)
    view = container(request).brand_settings.view(user.user_id, wanted)
    return _page(request, user, view, section_of(section), notice=SAID.get(saved))


@router.post("/brands/{brand_id}/settings")
def save_settings(
    request: Request, brand_id: str, form: Annotated[SettingsForm, Form()]
) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    require_csrf(request, user, form.csrf_token)
    wanted, service = _brand_id(brand_id), container(request).brand_settings
    if form.action == "save":
        saved = service.save(user.user_id, wanted, form.knobs())
        return _after(wanted, saved, form.section)
    try:
        view = service.view(user.user_id, wanted, form.knobs())
    except (ValidationError, InvalidSchedule):
        current = service.view(user.user_id, wanted)
        refused = _page(request, user, current, form.section, notice=SAID[Saved.INVALID])
        refused.status_code = status.HTTP_400_BAD_REQUEST
        return refused
    return _page(request, user, view, form.section, notice="Preview: not saved yet.")


@router.post("/brands/{brand_id}/settings/preset")
def apply_preset(
    request: Request,
    brand_id: str,
    preset: Annotated[Preset, Form()],
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    require_csrf(request, user, csrf_token)
    wanted = _brand_id(brand_id)
    saved = container(request).brand_settings.apply_preset(user.user_id, wanted, preset)
    return _after(wanted, saved, Section.GENERAL)


def _page(
    request: Request,
    user: CurrentUser,
    view: SettingsView | None,
    section: Section,
    *,
    notice: str | None,
) -> Response:
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return signed_in_page(
        request,
        user,
        "brand_settings.html",
        view=view,
        section=section,
        sections=[(s, name) for s, (name, _) in SECTIONS.items()],
        hidden=hidden(view.knobs, section),
        presets=list(Preset),
        intervals=sorted(INTERVALS_MINUTES),
        ranges=RANGES,
        sorts=list(ReviewSort),
        notice=notice,
    )


def _after(brand_id: uuid.UUID, saved: Saved, section: Section) -> Response:
    if saved is Saved.MISSING:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    shown = "" if section is Section.GENERAL else f"&section={section.value}"
    return RedirectResponse(f"/brands/{brand_id}/settings?saved={saved.value}{shown}", SEE_OTHER)


def _brand_id(text: str) -> uuid.UUID:
    """The id in the path; one that isn't an id at all is as missing as any other."""
    try:
        return uuid.UUID(text)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
