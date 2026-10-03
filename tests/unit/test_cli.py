import base64
import json
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from serpsense import __version__
from serpsense.adapters.db.replay_export import Exported
from serpsense.composition_replay import Replayed
from serpsense.domain.enums import LlmTask
from serpsense.entrypoints import cli
from serpsense.entrypoints.cli import app
from serpsense.ports.accounts import InvalidEmail
from serpsense.ports.llm_client import LlmCallFailed, LlmRequest
from serpsense.services.demo import Seeded
from serpsense.services.evals import EvalResult, Evaluator, GoldenBrand, GoldenItem, Split
from serpsense.services.replay import Played
from tests.factories import make_settings
from tests.unit.test_evals import answering, evaluator, item
from tests.unit.test_grouping_eval import BRAND, grouping_evaluator, mention, placing
from tests.unit.test_output_evals import DRAFT, EXPLAIN, FAQ, SAID
from tests.unit.test_output_evals import evaluator as output_evaluator

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


def replaying(asked: list[str], *, refuse: bool = False) -> Callable[..., Callable[[str], Any]]:
    def replayer(settings: object, celery: object) -> Callable[[str], Replayed]:
        def replay(email: str) -> Replayed:
            if refuse:
                raise InvalidEmail("not an email address")
            asked.append(email)
            seeded = Seeded(uuid.uuid4(), uuid.uuid4(), tuple(uuid.uuid4() for _ in range(4)))
            return Replayed(seeded, Counter({Played.PLAYED: 9, Played.ALREADY: 2}))

        return replay

    return replayer


@pytest.mark.parametrize("command", [["seed-demo"], ["replay", "load"]])
def test_in_replay_mode_seeding_plays_the_recordings(
    monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    asked: list[str] = []
    monkeypatch.setattr(cli, "build_settings", lambda: make_settings(serpsense_mode="replay"))
    monkeypatch.setattr(cli, "build_replayer", replaying(asked))
    result = runner.invoke(app, [*command, "--owner", "demo@example.com"])
    assert result.exit_code == 0 and asked == ["demo@example.com"]
    assert "Played 9 recorded scans (2 played before, 0 left for later)" in result.stdout
    assert "demo@example.com" not in result.stdout + result.stderr  # never echoed
    monkeypatch.setattr(cli, "build_replayer", replaying([], refuse=True))
    refused = runner.invoke(app, [*command, "--owner", "nobody"])
    assert refused.exit_code == 2 and "That isn't an email address." in refused.stderr


def test_replay_load_refuses_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "build_settings", make_settings)
    result = runner.invoke(app, ["replay", "load", "--owner", "demo@example.com"])
    assert result.exit_code == 2 and "only with SERPSENSE_MODE=replay" in result.stderr


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


def test_eval_groups_the_golden_mentions_and_writes_its_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    golden, reports = tmp_path / "golden", tmp_path / "reports"
    golden.mkdir()
    items = [mention(1, "refunds"), mention(2, "refunds"), mention(3, None)]
    (golden / "group_narratives.jsonl").write_text("\n".join(i.model_dump_json() for i in items))
    (golden / "group_narratives.brand.json").write_text(BRAND.model_dump_json())
    grouping, _ = grouping_evaluator(placing({"text 1": "new1", "text 2": "new1"}))
    monkeypatch.setattr(cli, "build_settings", make_settings)
    monkeypatch.setattr(cli, "build_grouping_evaluator", lambda s: (grouping, "claude-opus-5-5"))
    args = ["eval", "group_narratives", "--golden", str(golden), "--reports", str(reports)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0 and "Scored 3 of 3 items" in result.stdout
    (written,) = reports.iterdir()
    assert written.name.endswith("-group_narratives.md")
    assert "| Recall | 100.0% |" in written.read_text()


@pytest.mark.parametrize(
    "args", [["classify_autocomplete"], ["label_mentions", "--split", "train"]]
)
def test_eval_refuses_a_task_or_split_with_no_golden_set(args: list[str]) -> None:
    result = runner.invoke(app, ["eval", *args])
    assert result.exit_code == 2 and "No eval for" in result.stderr


class Exporter:
    """Answers the demo brands' recordings, and knows no other brand."""

    def export(self, slug: str) -> Exported:
        if slug == "nowhere":
            raise LookupError("exactly one live brand must have this slug")
        return Exported(text=f'{{"brand":"{slug}"}}\n', scans=2, answers=9, labels=40, narratives=3)


def test_replay_export_writes_one_recording_per_brand(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "build_settings", make_settings)
    monkeypatch.setattr(cli, "build_recording_export", lambda settings: Exporter())
    out = tmp_path / "recordings"
    result = runner.invoke(app, ["replay", "export", "--out", str(out)])
    assert result.exit_code == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "indrive.json",
        "namma-yatri.json",
        "ola.json",
        "rapido.json",
        "uber.json",
    ]  # the demo's brands by default: a re-export is one command
    assert (out / "ola.json").read_text(encoding="utf-8") == '{"brand":"ola"}\n'
    assert "ola: 2 scans (9 answers), 40 labels, 3 stories." in result.stdout
    one = ["replay", "export", "--brand", "nowhere", "--out", str(out)]
    refused = runner.invoke(app, one)
    assert refused.exit_code == 2 and "nowhere: exactly one live brand" in refused.stderr


def test_eval_scores_the_explanation_and_draft_cases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "explain_crisis.jsonl").write_text(EXPLAIN.model_dump_json() + "\n")
    (tmp_path / "draft_response.jsonl").write_text(DRAFT.model_dump_json() + "\n")

    def answer(request: LlmRequest) -> str:
        if request.task is LlmTask.EXPLAIN_CRISIS:
            return json.dumps({"explanation": SAID})
        return json.dumps({"text": FAQ, "cited": ["m1"]})

    run, _ = output_evaluator(answer)
    monkeypatch.setattr(cli, "build_settings", make_settings)
    monkeypatch.setattr(cli, "build_output_evaluator", lambda settings: (run, "claude-opus-5-5"))
    args = ["--golden", str(tmp_path), "--reports", str(tmp_path / "reports")]
    for task in ("explain_crisis", "draft_response"):
        result = runner.invoke(app, ["eval", task, *args])
        assert result.exit_code == 0 and "Scored 1 of 1 items" in result.stdout
    written = sorted(p.name for p in (tmp_path / "reports").iterdir())
    assert [name.split("-", 3)[-1] for name in written] == [
        "draft_response.md", "explain_crisis.md",
    ]  # fmt: skip


def test_an_output_eval_without_a_key_or_with_unanswered_cases_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "explain_crisis.jsonl").write_text(EXPLAIN.model_dump_json() + "\n")
    args = ["eval", "explain_crisis", "--golden", str(tmp_path), "--reports", str(tmp_path)]
    monkeypatch.setattr(cli, "build_settings", make_settings)
    no_key = runner.invoke(app, args)
    assert no_key.exit_code == 2 and "ANTHROPIC_API_KEY" in no_key.stderr
    timeout = LlmCallFailed("llm.timeout", retryable=True, latency_ms=5)
    run, _ = output_evaluator(lambda request: timeout)
    monkeypatch.setattr(cli, "build_output_evaluator", lambda settings: (run, "claude-opus-5-5"))
    unanswered = runner.invoke(app, args)
    assert unanswered.exit_code == 1 and "Scored 0 of 1 items" in unanswered.stdout
