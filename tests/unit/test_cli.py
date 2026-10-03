import base64
import uuid
from collections.abc import Callable

import pytest
from typer.testing import CliRunner

from serpsense import __version__
from serpsense.entrypoints import cli
from serpsense.entrypoints.cli import app
from serpsense.ports.accounts import InvalidEmail
from serpsense.services.demo import Seeded
from tests.factories import make_settings

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_gen_secrets_prints_valid_fresh_values() -> None:
    first = runner.invoke(app, ["gen-secrets"])
    second = runner.invoke(app, ["gen-secrets"])
    assert first.exit_code == 0
    lines = dict(line.split("=", 1) for line in first.stdout.strip().splitlines())
    assert len(lines["SECRET_KEY"]) >= 32
    assert len(base64.urlsafe_b64decode(lines["OUTBOX_ENCRYPTION_KEYS"])) == 32
    assert first.stdout != second.stdout


def test_seed_demo_seeds_under_the_owner_and_says_what_comes_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = Seeded(uuid.uuid4(), uuid.uuid4(), tuple(uuid.uuid4() for _ in range(4)))
    asked: list[str] = []

    def seeder(settings: object, celery: object) -> Callable[[str], Seeded]:
        def seed(email: str) -> Seeded:
            asked.append(email)
            return seeded

        return seed

    monkeypatch.setattr(cli, "build_settings", make_settings)
    monkeypatch.setattr(cli, "build_seeder", seeder)
    result = runner.invoke(app, ["seed-demo", "--owner", "demo@example.com"])
    assert result.exit_code == 0
    assert asked == ["demo@example.com"]
    assert f"Seeded Ola ({seeded.brand_id}) and 4 competitors." in result.stdout
    assert "demo@example.com" not in result.stdout + result.stderr  # never echoed


def test_seed_demo_refuses_what_isnt_an_email_address(monkeypatch: pytest.MonkeyPatch) -> None:
    def seeder(settings: object, celery: object) -> Callable[[str], Seeded]:
        def refuse(email: str) -> Seeded:
            raise InvalidEmail("not an email address")

        return refuse

    monkeypatch.setattr(cli, "build_settings", make_settings)
    monkeypatch.setattr(cli, "build_seeder", seeder)
    result = runner.invoke(app, ["seed-demo", "--owner", "nobody"])
    assert result.exit_code == 2
    assert "That isn't an email address." in result.stderr
    assert "nobody" not in result.stdout + result.stderr
