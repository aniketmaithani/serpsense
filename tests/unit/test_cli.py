import base64
import json
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from serpsense import __version__
from serpsense.adapters.db.replay_export import Exported
from serpsense.entrypoints import cli
from serpsense.entrypoints.cli import app
from serpsense.ports.accounts import InvalidEmail
from serpsense.services.demo import Seeded
from serpsense.services.evals import EvalResult, Evaluator, GoldenBrand, GoldenItem, Split
from tests.factories import make_settings
from tests.unit.test_evals import answering, evaluator, item

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


def test_score_backlog_says_how_many_scans_it_scored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "build_settings", make_settings)
    monkeypatch.setattr(cli, "build_scorer", lambda settings, celery: lambda: 5)
    result = runner.invoke(app, ["score-backlog"])
    assert result.exit_code == 0
    assert "Scored 5 scans." in result.stdout


def test_eval_scores_a_golden_split_and_writes_its_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    golden, reports = tmp_path / "golden", tmp_path / "reports"
    golden.mkdir()
    items = [item(1), item(2, 1)]
    other = item(3).model_copy(update={"split": Split.DEV})
    (golden / "label_mentions.jsonl").write_text(
        "\n".join(i.model_dump_json() for i in [*items, other]) + "\n"
    )
    (golden / "label_mentions.brand.json").write_text(json.dumps({"name": "Ola"}))
    run, llm = evaluator(answering([*items, other]))
    monkeypatch.setattr(cli, "build_settings", make_settings)
    monkeypatch.setattr(cli, "build_evaluator", lambda settings: (run, "claude-opus-5-5"))
    args = ["eval", "label_mentions", "--golden", str(golden), "--reports", str(reports)]
    result = runner.invoke(app, [*args, "--cap-cents", "5"])
    assert result.exit_code == 0 and "Scored 2 of 2 items" in result.stdout  # the test split only
    (written,) = reports.iterdir()
    assert (
        written.name.endswith("-label_mentions.md") and "| sentiment | 2/2 |" in written.read_text()
    )
    assert len(llm.requests) == 1


class CapturingEvaluator:
    """The real evaluator, noting the cap it was given."""

    def __init__(self, run: Evaluator) -> None:
        self.run, self.caps = run, list[int]()

    def label_mentions(
        self, items: Sequence[GoldenItem], brand: GoldenBrand, *, cap_micros: int
    ) -> EvalResult:
        self.caps.append(cap_micros)
        return self.run.label_mentions(items, brand, cap_micros=cap_micros)


def test_eval_passes_the_cap_in_micros_and_fails_when_items_go_unanswered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "label_mentions.jsonl").write_text(item(1).model_dump_json() + "\n")
    (tmp_path / "label_mentions.brand.json").write_text(json.dumps({"name": "Ola"}))
    run, _ = evaluator(lambda request: json.dumps({"labels": []}))  # answers nothing
    capturing = CapturingEvaluator(run)
    monkeypatch.setattr(cli, "build_settings", make_settings)
    monkeypatch.setattr(cli, "build_evaluator", lambda settings: (capturing, "claude-opus-5-5"))
    args = ["eval", "label_mentions", "--golden", str(tmp_path), "--reports", str(tmp_path)]
    result = runner.invoke(app, [*args, "--cap-cents", "7"])
    assert result.exit_code == 1 and "Scored 0 of 1 items" in result.stdout
    assert capturing.caps == [70_000]  # 7 US cents in micros


@pytest.mark.parametrize("args", [["group_narratives"], ["label_mentions", "--split", "train"]])
def test_eval_refuses_a_task_or_split_with_no_golden_set(args: list[str]) -> None:
    result = runner.invoke(app, ["eval", *args])
    assert result.exit_code == 2 and "No eval for" in result.stderr


class Exporter:
    """Answers one brand's recording, and knows no other brand."""

    def export(self, slug: str) -> Exported:
        if slug != "ola":
            raise LookupError("exactly one live brand must have this slug")
        return Exported(text='{"format":1}\n', scans=2, answers=9, labels=40)


def test_replay_export_writes_the_recording(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "build_settings", make_settings)
    monkeypatch.setattr(cli, "build_recording_export", lambda settings: Exporter())
    out = tmp_path / "recordings" / "ola.json"
    result = runner.invoke(app, ["replay", "export", "--brand", "ola", "--out", str(out)])
    assert result.exit_code == 0 and "Recorded 2 scans (9 answers) and 40 labels" in result.stdout
    assert out.read_text(encoding="utf-8") == '{"format":1}\n'
    missing = ["replay", "export", "--brand", "uber", "--out", str(out)]
    refused = runner.invoke(app, missing)
    assert refused.exit_code == 2 and "exactly one live brand" in refused.stderr
