"""The operator console's pages at /admin (ADR-0014, ADR-0015): off (404) without ADMIN_PASSWORD.

The login form carries the sign-in pages' double-submit token; the session is a signed cookie
(SameSite=Strict), and every write carries its CSRF token. Nothing here is cached or indexed.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from serpsense.domain.console import SESSION_LENGTH
from serpsense.domain.enums import AccessDecision, SignupMode
from serpsense.entrypoints.web.pages import page
from serpsense.entrypoints.web.session import (
    clear_console_session,
    console_token,
    container,
    form_token,
    keep_form_token,
    network,
    require_form_token,
    set_console_session,
)
from serpsense.services.console import Console

router = APIRouter(prefix="/admin")
SEE_OTHER = status.HTTP_303_SEE_OTHER
Field = Annotated[str, Form()]
DECISIONS = {"approve": AccessDecision.APPROVED, "reject": AccessDecision.REJECTED}
# The operator sees "approval required" where the code says invite mode (ADR-0015).
MODES = {"open": SignupMode.OPEN, "approval": SignupMode.INVITE}


@router.get("/login")
def login_form(request: Request) -> Response:
    console = _console(request)
    if _session(request, console) is not None:
        return _private(RedirectResponse("/admin", SEE_OTHER))
    return _login(request)


@router.post("/login")
def log_in(request: Request, form_token: Field = "", password: Field = "") -> Response:
    console = _console(request)
    require_form_token(request, form_token)
    token = console.gate.log_in(password, network(request))
    if token is None:
        return _login(request, status_code=400, error="That password didn't work.")
    response = RedirectResponse("/admin", SEE_OTHER)
    set_console_session(request, response, token, seconds=int(SESSION_LENGTH.total_seconds()))
    return _private(response)


@router.post("/logout")
def log_out(request: Request, csrf_token: Field = "") -> Response:
    """Ends every console session, not just this browser's (ADR-0014)."""
    console = _console(request)
    token = _session(request, console)
    if token is not None:
        _require_csrf(console, token, csrf_token)
        console.gate.log_out()
    response = RedirectResponse("/admin/login", SEE_OTHER)
    clear_console_session(request, response)
    return _private(response)


@router.get("")
def overview(request: Request) -> Response:
    console = _console(request)
    token = _session(request, console)
    if token is None:
        return _private(RedirectResponse("/admin/login", SEE_OTHER))
    csrf = console.gate.csrf_token(token)
    return _private(page(request, "console.html", view=console.snapshot(), csrf_token=csrf))


@router.post("/access/{request_id}/{verb}")
def decide(request: Request, request_id: uuid.UUID, verb: str, csrf_token: Field = "") -> Response:
    console = _console(request)
    token = _session(request, console)
    if token is None:
        return _private(RedirectResponse("/admin/login", SEE_OTHER))
    _require_csrf(console, token, csrf_token)
    decision = DECISIONS.get(verb)
    if decision is None or not console.decide(request_id, decision):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return _private(RedirectResponse("/admin", SEE_OTHER))


@router.post("/signup/{choice}")
def switch_signup(request: Request, choice: str, csrf_token: Field = "") -> Response:
    console = _console(request)
    token = _session(request, console)
    if token is None:
        return _private(RedirectResponse("/admin/login", SEE_OTHER))
    _require_csrf(console, token, csrf_token)
    mode = MODES.get(choice)
    if mode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    console.switch_signup(mode)
    return _private(RedirectResponse("/admin", SEE_OTHER))


def _console(request: Request) -> Console:
    found = container(request).console
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return found


def _session(request: Request, console: Console) -> str | None:
    token = console_token(request)
    return token if console.gate.signed_in(token) else None


def _require_csrf(console: Console, token: str, submitted: str) -> None:
    if not console.gate.csrf_valid(token, submitted):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This form has expired; reload the page.")


def _login(request: Request, *, status_code: int = 200, error: str = "") -> Response:
    token = form_token(request)
    response = page(
        request, "console_login.html", status_code=status_code, form_token=token, error=error
    )
    keep_form_token(request, response, token)
    return _private(response)


def _private(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response
