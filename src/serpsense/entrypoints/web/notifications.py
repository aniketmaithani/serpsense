"""The notifications centre (BUILD_PLAN §13): the signed-in user's notifications and read marks.
Marking reads is a CSRF-checked form; another user's notification marks nothing."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from serpsense.entrypoints.web.pages import signed_in_page
from serpsense.entrypoints.web.session import container, current_user, require_csrf

router = APIRouter()
SEE_OTHER = status.HTTP_303_SEE_OTHER
SHOWN = 100


@router.get("/notifications")
def notifications(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    rows = container(request).inbox.latest(user.user_id, limit=SHOWN)
    return signed_in_page(request, user, "notifications.html", rows=rows)


@router.post("/notifications/read")
def mark_read(
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
    notification_id: Annotated[str, Form()] = "",
    and_older: Annotated[bool, Form()] = False,
) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    require_csrf(request, user, csrf_token)
    try:
        one = uuid.UUID(notification_id)
    except ValueError:
        return RedirectResponse("/notifications", SEE_OTHER)  # nothing that could be marked
    container(request).inbox.mark_read(user.user_id, one, and_older=and_older)
    return RedirectResponse("/notifications", SEE_OTHER)
