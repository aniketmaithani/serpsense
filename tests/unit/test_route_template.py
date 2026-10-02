import pytest
from starlette.requests import Request
from starlette.routing import Route

from serpsense.entrypoints.web.middleware import UNMATCHED_ROUTE, route_template

pytestmark = pytest.mark.unit


def _request(scope_extra: dict[str, object]) -> Request:
    return Request(
        {"type": "http", "method": "GET", "path": "/brands/123", "headers": [], **scope_extra}
    )


def test_route_template_uses_matched_route_not_raw_path() -> None:
    route = Route("/brands/{brand_id}", endpoint=lambda _: None)
    assert route_template(_request({"route": route})) == "/brands/{brand_id}"


def test_route_template_falls_back_when_unmatched() -> None:
    assert route_template(_request({})) == UNMATCHED_ROUTE
