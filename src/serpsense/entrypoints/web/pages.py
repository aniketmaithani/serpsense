"""Server-rendered pages (ADR-0002): Jinja with autoescaping, plain CSS, no inline scripts."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).parent
STATIC = HERE / "static"
TEMPLATES = Environment(
    loader=FileSystemLoader(HERE / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def page(
    request: Request, template: str, *, status_code: int = 200, **context: Any
) -> HTMLResponse:
    html = TEMPLATES.get_template(template).render(request=request, **context)
    return HTMLResponse(html, status_code=status_code)
