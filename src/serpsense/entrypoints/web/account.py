"""Settings → Account (ADR-0009, ADR-0013): sign out everywhere, or delete the account.

Deleting takes two steps: a code emailed to the account's address, then that code with the
address typed out. A wrong code and a wrong address get the same answer. An email being sent to
the user that holds things up past the lock timeout gets a "try again in a minute" (503, with
Retry-After) and changes nothing. Once the account is gone the browser's session cookie is
cleared and the sign-in page says so. Every write carries the session's CSRF token.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from serpsense.entrypoints.web.pages import signed_in_page
from serpsense.entrypoints.web.session import (
    clear_session,
    container,
    current_user,
    network,
    require_csrf,
    sessions,
)
from serpsense.services.accounts import Deletion
from serpsense.services.sessions import CurrentUser

router = APIRouter(prefix="/settings/account")
SEE_OTHER = status.HTTP_303_SEE_OTHER
Field = Annotated[str, Form()]
REFUSED = "That code or address didn't match. Check both, or ask for a new code."
BUSY = "An email to you is being sent right now. Nothing was deleted: try again in a minute."


@router.get("")
def account(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    return _page(request, user)


@router.post("/sign-out-everywhere")
def sign_out_everywhere(request: Request, csrf_token: Field = "") -> Response:
    user = current_user(request)
    if user is not None:
        require_csrf(request, user, csrf_token)
        sessions(request).log_out_everywhere(user, network(request))
    response = RedirectResponse("/login", SEE_OTHER)
    clear_session(request, response)
    return response


@router.post("/delete/code")
def send_code(request: Request, csrf_token: Field = "") -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    require_csrf(request, user, csrf_token)
    container(request).accounts.send_code(user, network(request))
    return _page(request, user, confirming=True)


@router.post("/delete")
def delete(
    request: Request, csrf_token: Field = "", code: Field = "", email: Field = ""
) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    require_csrf(request, user, csrf_token)
    outcome = container(request).accounts.delete(user, code, email, network(request))
    if outcome is Deletion.BUSY:
        response = _page(request, user, status_code=503, confirming=True, error=BUSY)
        response.headers["Retry-After"] = "60"
        return response
    if outcome is Deletion.REFUSED:
        return _page(request, user, status_code=400, confirming=True, error=REFUSED)
    response = RedirectResponse("/login?deleted=1", SEE_OTHER)
    clear_session(request, response)
    return response


def _page(
    request: Request,
    user: CurrentUser,
    *,
    status_code: int = 200,
    confirming: bool = False,
    error: str | None = None,
) -> Response:
    return signed_in_page(
        request,
        user,
        "account.html",
        status_code=status_code,
        confirming=confirming,
        error=error,
    )
