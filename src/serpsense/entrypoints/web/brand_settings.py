"""A brand's search settings page (BUILD_PLAN §6, §13): the knobs a scan uses, its schedule, a
preset to start from, and the searches it would cost, previewed before saving. The owner's only;
anyone else's brand is a 404. Every write carries the session's CSRF token."""

import uuid
from collections.abc import Mapping
from typing import Annotated, Literal

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ValidationError, field_validator

from serpsense.domain.schedule import INTERVALS_MINUTES, InvalidSchedule
from serpsense.domain.settings.search import Preset
from serpsense.entrypoints.web.pages import signed_in_page
from serpsense.entrypoints.web.session import container, current_user, require_csrf
from serpsense.services.brand_settings import Knobs, Saved, SettingsView
from serpsense.services.sessions import CurrentUser

router = APIRouter()
SEE_OTHER = status.HTTP_303_SEE_OTHER
SAID: Mapping[str, str] = {
    Saved.SAVED: "Saved: the next scan uses these settings.",
    Saved.UNCHANGED: "Nothing changed.",
    Saved.INVALID: "Those settings can't be used for a scan.",
}


class SettingsForm(BaseModel):
    """The form as posted: unticked boxes are absent."""

    csrf_token: str = ""
    action: Literal["save", "preview"] = "save"
    max_searches: int  # the service refuses values a scan can't use
    interval_minutes: int | None = None  # empty: manual scans only
    play_review_pages: int = 0
    search_page: bool = False
    ai_overview: bool = False
    autocomplete: bool = False
    news: bool = False
    trends: bool = False
    play: bool = False
    maps: bool = False

    @field_validator("interval_minutes", mode="before")
    @classmethod
    def _manual(cls, value: object) -> object:
        return None if value == "" else value

    def knobs(self) -> Knobs:
        return Knobs(**self.model_dump(exclude={"csrf_token", "action"}))


@router.get("/brands/{brand_id}/settings")
def settings_page(request: Request, brand_id: str, saved: str = "") -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    wanted = _brand_id(brand_id)
    view = container(request).brand_settings.view(user.user_id, wanted)
    return _page(request, user, view, notice=SAID.get(saved))


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
        return _after(wanted, saved)
    try:
        view = service.view(user.user_id, wanted, form.knobs())
    except (ValidationError, InvalidSchedule):
        current = service.view(user.user_id, wanted)
        return _page(request, user, current, notice=SAID[Saved.INVALID], status_code=400)
    return _page(request, user, view, notice="Preview: not saved yet.")


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
    return _after(
        wanted, container(request).brand_settings.apply_preset(user.user_id, wanted, preset)
    )


def _page(
    request: Request,
    user: CurrentUser,
    view: SettingsView | None,
    *,
    notice: str | None,
    status_code: int = 200,
) -> Response:
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    intervals = sorted(INTERVALS_MINUTES)
    return signed_in_page(
        request,
        user,
        "brand_settings.html",
        view=view,
        presets=list(Preset),
        intervals=intervals,
        notice=notice,
        status_code=status_code,
    )


def _after(brand_id: uuid.UUID, saved: Saved) -> Response:
    if saved is Saved.MISSING:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return RedirectResponse(f"/brands/{brand_id}/settings?saved={saved.value}", SEE_OTHER)


def _brand_id(text: str) -> uuid.UUID:
    """The id in the path; one that isn't an id at all is as missing as any other."""
    try:
        return uuid.UUID(text)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
