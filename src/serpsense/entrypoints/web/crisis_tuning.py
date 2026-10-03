"""How a brand's crisis is picked up, and the knobs that tune how it is read (docs/scoring.md):
the signals table on the brand page, and the tuning page. The owner's only; anyone else's brand
is a 404. Every write carries the session's CSRF token."""

import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from serpsense.domain.enums import CrisisComponent
from serpsense.domain.scoring.crisis import CRISIS_WEIGHTS_BP
from serpsense.domain.scoring.tuning import DEFAULT_TUNING, RANGES, CrisisTuning, InvalidTuning
from serpsense.entrypoints.web.pages import signed_in_page
from serpsense.entrypoints.web.session import container, current_user, require_csrf
from serpsense.ports.overview import BrandPage
from serpsense.services.brand_settings import Saved
from serpsense.services.crisis_tuning import TuningView
from serpsense.services.sessions import CurrentUser

router = APIRouter()
SEE_OTHER = status.HTTP_303_SEE_OTHER
SAID: Mapping[str, str] = {
    Saved.SAVED: "Saved: the brand's levels follow it now, its alerts from the next scan.",
    Saved.UNCHANGED: "Nothing changed.",
}
REFUSED = "Not saved: keep each value in its range, and start high above medium."
SIGNALS: Mapping[CrisisComponent, tuple[str, str]] = {
    CrisisComponent.VELOCITY: (
        "Velocity",
        "New negative mentions against the brand's usual: 0 at or below it, 100 at four times it.",
    ),
    CrisisComponent.SPREAD: (
        "Spread",
        "Share of surfaces whose new negatives exceed that surface's usual.",
    ),
    CrisisComponent.AUTOCOMPLETE: (
        "Negative autocomplete",
        "A negative suggestion new this scan: 100, 90, 80 in the top three places, 70 below.",
    ),
    CrisisComponent.TRENDS: (
        "Rising negative Trends query",
        "A negative rising query new this scan: 60, plus 20 for each more.",
    ),
    CrisisComponent.PRESS: (
        "Press in 48 hours",
        "Negative articles new this scan from the last 48 hours: half their severities.",
    ),
}


@dataclass(frozen=True)
class Signal:
    name: str
    measures: str
    weight: int  # percent of the crisis score
    value: int  # 0-100, the latest scored scan's
    points: int  # what it added to the crisis score


def signals(page: BrandPage) -> list[Signal]:
    """The latest scored scan's crisis signals, as the brand page explains them."""
    shown = []
    for component, (name, measures) in SIGNALS.items():
        weight, value = CRISIS_WEIGHTS_BP[component], page.components.get(component, 0)
        points = (weight * value + 5000) // 10000  # rounded half up, as the score is
        shown.append(Signal(name, measures, weight // 100, value, points))
    return shown


class TuningForm(BaseModel):
    csrf_token: str = ""
    action: Literal["save", "reset"] = "save"
    warm_up_scans: int
    medium_at: int
    high_at: int
    cooldown_hours: int
    spread_mentions: int
    spread_surfaces: int

    def tuning(self) -> CrisisTuning:
        """Raises InvalidTuning for values out of range; a reset is the defaults."""
        if self.action == "reset":
            return DEFAULT_TUNING
        return CrisisTuning(**self.model_dump(exclude={"csrf_token", "action"}))


@router.get("/brands/{brand_id}/crisis")
def tuning_page(request: Request, brand_id: str, saved: str = "") -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    view = container(request).crisis_tuning.view(user.user_id, _brand_id(brand_id))
    return _page(request, user, view, notice=SAID.get(saved))


@router.post("/brands/{brand_id}/crisis")
def save_tuning(request: Request, brand_id: str, form: Annotated[TuningForm, Form()]) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    require_csrf(request, user, form.csrf_token)
    wanted, service = _brand_id(brand_id), container(request).crisis_tuning
    try:
        tuning = form.tuning()
    except InvalidTuning:
        view = service.view(user.user_id, wanted)
        return _page(request, user, view, notice=REFUSED, status_code=400)
    saved = service.save(user.user_id, wanted, tuning)
    if saved is Saved.MISSING:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return RedirectResponse(f"/brands/{wanted}/crisis?saved={saved.value}", SEE_OTHER)


def _page(
    request: Request,
    user: CurrentUser,
    view: TuningView | None,
    *,
    notice: str | None,
    status_code: int = 200,
) -> Response:
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return signed_in_page(
        request,
        user,
        "crisis_tuning.html",
        view=view,
        knobs=asdict(view.tuning),
        defaults=asdict(DEFAULT_TUNING),
        ranges=RANGES,
        notice=notice,
        status_code=status_code,
    )


def _brand_id(text: str) -> uuid.UUID:
    try:
        return uuid.UUID(text)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
