"""Add a brand (BUILD_PLAN §13): a CSRF-checked form; the new brand's page follows."""

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator

from serpsense.domain.schedule import INTERVALS_MINUTES
from serpsense.domain.settings.search import Preset
from serpsense.entrypoints.web.pages import signed_in_page
from serpsense.entrypoints.web.session import container, current_user, require_csrf
from serpsense.services.brands import MAX_COMPETITORS, BrandRequest, InvalidBrand
from serpsense.services.sessions import CurrentUser

router = APIRouter()
SEE_OTHER = status.HTTP_303_SEE_OTHER


class BrandForm(BaseModel):
    csrf_token: str = ""
    name: str = ""
    play_app: str = ""
    competitors: str = ""  # one per line
    preset: Preset = Preset.STANDARD
    interval_minutes: int | None = None  # empty: manual only

    @field_validator("interval_minutes", mode="before")
    @classmethod
    def _manual(cls, value: object) -> object:
        return None if value == "" else value

    def request(self) -> BrandRequest:
        return BrandRequest(
            name=self.name,
            play_app=self.play_app.strip() or None,
            competitors=tuple(self.competitors.splitlines()),
            preset=self.preset,
            interval_minutes=self.interval_minutes,
        )


@router.get("/brands/new")
def new_brand(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    return _form(request, user, error=None, form=BrandForm())


@router.post("/brands/new")
def add_brand(request: Request, form: Annotated[BrandForm, Form()]) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    require_csrf(request, user, form.csrf_token)
    if form.interval_minutes is not None and form.interval_minutes not in INTERVALS_MINUTES:
        return _form(request, user, error="Pick a schedule from the list.", form=form)
    try:
        brand_id = container(request).brands.add(user.user_id, form.request())
    except InvalidBrand as exc:
        return _form(request, user, error=str(exc), form=form)
    return RedirectResponse(f"/brands/{brand_id}?scan=added", SEE_OTHER)


def _form(request: Request, user: CurrentUser, *, error: str | None, form: BrandForm) -> Response:
    return signed_in_page(
        request,
        user,
        "add_brand.html",
        form=form,
        error=error,
        presets=list(Preset),
        intervals=sorted(INTERVALS_MINUTES),
        most=MAX_COMPETITORS,
        status_code=400 if error else 200,
    )
