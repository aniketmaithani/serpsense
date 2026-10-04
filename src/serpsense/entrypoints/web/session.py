"""Who is asking, and whether a form really came from our page (ADR-0009).

The session cookie holds a random token (HttpOnly, SameSite=Lax; in production Secure and named
__Host-, so no subdomain can set or read it). A signed-in
write carries the session's CSRF token, in a form field or the X-CSRF-Token header. The two sign-in
forms come before any session, so they carry a double-submit token instead: a random value in a
strict cookie that the form must echo. The operator console (ADR-0014) has its own cookie, signed
rather than stored, SameSite=Strict, with the same prefix and flags.
"""

import hmac
import secrets
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Literal, cast

from fastapi import HTTPException, Request, Response, status

from serpsense.composition import Container
from serpsense.ports.audit import Network
from serpsense.services.auth import SignIn
from serpsense.services.sessions import CurrentUser, SessionGuard

SESSION, FORM, CONSOLE = "serpsense_session", "serpsense_form", "serpsense_admin"


def container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


def sign_in(request: Request) -> SignIn:
    return container(request).sign_in


def sessions(request: Request) -> SessionGuard:
    return container(request).sessions


def network(request: Request) -> Network:
    host = request.client.host if request.client else None
    return Network(_ip(host), request.headers.get("user-agent"))


def cookie_name(request: Request, name: str) -> str:
    return f"__Host-{name}" if container(request).settings.is_production else name


def current_user(request: Request) -> CurrentUser | None:
    return sessions(request).current(request.cookies.get(cookie_name(request, SESSION)))


def set_session(request: Request, response: Response, token: str) -> None:
    settings = container(request).settings
    response.set_cookie(
        cookie_name(request, SESSION),
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )


def clear_session(request: Request, response: Response) -> None:
    _forget(request, response, SESSION, samesite="lax")


def console_token(request: Request) -> str | None:
    return request.cookies.get(cookie_name(request, CONSOLE))


def set_console_session(request: Request, response: Response, token: str, *, seconds: int) -> None:
    secure = container(request).settings.is_production
    name = cookie_name(request, CONSOLE)
    response.set_cookie(
        name, token, max_age=seconds, httponly=True, secure=secure, samesite="strict"
    )


def clear_console_session(request: Request, response: Response) -> None:
    _forget(request, response, CONSOLE, samesite="strict")


def require_csrf(request: Request, user: CurrentUser, submitted: str | None) -> None:
    token = submitted or request.headers.get("x-csrf-token") or ""
    if not sessions(request).csrf_valid(user, token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This form has expired; reload the page.")


def form_token(request: Request) -> str:
    """The sign-in forms' double-submit token: the one this browser has, or a new one."""
    return request.cookies.get(cookie_name(request, FORM)) or secrets.token_urlsafe(32)


def keep_form_token(request: Request, response: Response, token: str) -> None:
    secure = container(request).settings.is_production
    name = cookie_name(request, FORM)
    response.set_cookie(name, token, httponly=True, secure=secure, samesite="strict")


def drop_form_token(request: Request, response: Response) -> None:
    _forget(request, response, FORM, samesite="strict")


def require_form_token(request: Request, submitted: str) -> None:
    expected = request.cookies.get(cookie_name(request, FORM), "")
    if not expected or not hmac.compare_digest(
        expected.encode(), submitted.encode("utf-8", "replace")
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This form has expired; reload the page.")


def _forget(
    request: Request, response: Response, name: str, *, samesite: Literal["lax", "strict"]
) -> None:
    """Delete a cookie with the attributes it was set with: a browser lets only a Secure cookie
    replace a __Host- one, so a plain deletion would leave it in place in production."""
    secure = container(request).settings.is_production
    name = cookie_name(request, name)
    response.delete_cookie(name, secure=secure, httponly=True, samesite=samesite)


def _ip(host: str | None) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(host) if host else None
    except ValueError:  # a test client, or a proxy that sent something else
        return None
