import base64

import pytest
from typer.testing import CliRunner

from serpsense import __version__
from serpsense.entrypoints.cli import app

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
