"""Real uvicorn server: crashes are logged once, through the scrubber (AGENTS.md §6, §8).

uvicorn configures its own log handlers before the app factory runs; this guards the fix that
routes them through our scrubbing handler and drops uvicorn's duplicate exception line.
"""

import contextlib
import io
import logging
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from serpsense.composition import build_settings
from serpsense.entrypoints.web.app import create_app
from tests.factories import make_container, make_settings

pytestmark = pytest.mark.integration


def _factory() -> FastAPI:
    app = create_app(make_container(build_settings(make_settings())))

    @app.get("/boom")
    def boom(request: Request) -> None:
        raise RuntimeError(f"upstream failed for {request.url}")

    @app.get("/stream")
    def stream() -> StreamingResponse:
        def chunks() -> Iterator[bytes]:
            yield b"x"
            raise RuntimeError("midstream failure api_key=leakstream42")

        return StreamingResponse(chunks())

    return app


@pytest.fixture
def server_url() -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app=_factory, factory=True, host="127.0.0.1", port=port, access_log=False)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def _capture_root_handler_output() -> io.StringIO:
    """Point our root StreamHandler at a buffer (pytest's capture streams close between phases)."""
    buffer = io.StringIO()
    for handler in logging.getLogger().handlers:
        if type(handler) is logging.StreamHandler:
            handler.setStream(buffer)
    return buffer


@pytest.mark.usefixtures("restore_logging")
def test_crashes_are_logged_once_and_scrubbed(server_url: str) -> None:
    logs = _capture_root_handler_output()
    response = httpx.get(f"{server_url}/boom?api_key=leakquery42")
    # The client may or may not see a broken stream; only the server-side logs matter here.
    with contextlib.suppress(httpx.HTTPError):
        httpx.get(f"{server_url}/stream")
    time.sleep(0.3)

    assert response.status_code == 500
    output = logs.getvalue()
    assert "leakquery42" not in output
    assert "leakstream42" not in output
    assert output.count("http.request_failed") == 2
    assert "Exception in ASGI application" not in output
