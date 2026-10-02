"""Shared test fixtures."""

import pytest

from serpsense.config import Settings
from tests.factories import make_settings


@pytest.fixture
def settings() -> Settings:
    return make_settings()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Integration tests talk to containers, so they may open sockets."""
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(pytest.mark.enable_socket)
