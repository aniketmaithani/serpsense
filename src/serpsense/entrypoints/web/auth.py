"""Sign-in pages (ADR-0009): ask for a code, enter it, sign out.

The verify page is rendered straight from the request-code form, so the email never travels in a
URL (and so never in a log). Every answer to "send me a code" looks the same, and no sign-in page
is cached. Signing in ends any session the browser already had.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from serpsense.entrypoints.web.pages import page
from serpsense.entrypoints.web.session import (
    clear_session,
    current_user,
    drop_form_token,
    form_token,
    keep_form_token,
    network,
    require_csrf,
    require_form_token,
    sessions,
    set_session,
    sign_in,
)
from serpsense.ports.accounts import InvalidEmail

router = APIRouter()
SEE_OTHER = status.HTTP_303_SEE_OTHER
Field = Annotated[str, Form()]


@router.get("/login")
def login_form(request: Request) -> Response:
    if current_user(request) is not None:
        return RedirectResponse("/", SEE_OTHER)
    return _form(request, "login.html")


@router.post("/login")
def request_code(request: Request, form_token: Field = "", email: Field = "") -> Response:
    require_form_token(request, form_token)
    try:
        sign_in(request).request_code(email, network(request))
    except InvalidEmail:
        error = "That doesn't look like an email address."
        return _form(request, "login.html", status_code=400, email=email, error=error)
    return _form(request, "verify.html", email=email.strip())


@router.post("/verify")
def verify(
    request: Request, form_token: Field = "", email: Field = "", code: Field = ""
) -> Response:
    require_form_token(request, form_token)
    previous = current_user(request)
    signed_in = sign_in(request).verify(email, code.strip(), network(request))
    if signed_in is None:
        error = "That code didn't work. Check it, or ask for a new one."
        return _form(request, "verify.html", status_code=400, email=email, error=error)
    if previous is not None:
        sessions(request).log_out(previous, network(request))
    response = RedirectResponse("/", SEE_OTHER)
    set_session(request, response, signed_in.token)
    drop_form_token(request, response)
    return response


@router.post("/logout")
def log_out(request: Request, csrf_token: Field = "") -> Response:
    user = current_user(request)
    if user is not None:
        require_csrf(request, user, csrf_token)
        sessions(request).log_out(user, network(request))
    response = RedirectResponse("/login", SEE_OTHER)
    clear_session(request, response)
    return response


def _form(request: Request, template: str, *, status_code: int = 200, **context: str) -> Response:
    token = form_token(request)
    response = page(request, template, status_code=status_code, form_token=token, **context)
    keep_form_token(request, response, token)
    response.headers["Cache-Control"] = "no-store"
    return response
