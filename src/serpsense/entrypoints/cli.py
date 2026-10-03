"""Command-line interface."""

import base64
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Annotated

import typer

from serpsense import __version__
from serpsense.composition import (
    build_celery,
    build_evaluator,
    build_grouping_evaluator,
    build_scorer,
    build_seeder,
    build_settings,
)
from serpsense.config import ConfigError
from serpsense.domain.enums import LlmTask
from serpsense.ports.accounts import InvalidEmail
from serpsense.services.evals import GoldenBrand, GoldenItem, Split, report
from serpsense.services.grouping_eval import GoldenMention, GroupingBrand, grouping_report

GOLDEN, REPORTS = Path("evals/golden"), Path("evals/reports")

app = typer.Typer(help="SerpSense command-line tools.", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the SerpSense version."""
    typer.echo(__version__)


@app.command("gen-secrets")
def gen_secrets() -> None:
    """Print fresh SECRET_KEY and OUTBOX_ENCRYPTION_KEYS lines to append to .env."""
    typer.echo(f"SECRET_KEY={secrets.token_urlsafe(48)}")
    # A Fernet key is 32 random bytes, URL-safe base64 encoded.
    fernet_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    typer.echo(f"OUTBOX_ENCRYPTION_KEYS={fernet_key}")


@app.command("seed-demo")
def seed_demo(
    owner: str = typer.Option(..., help="The demo owner's email address; created if new."),
) -> None:
    """Set up the Ola demo (BUILD_PLAN §22): Ola and four competitors on a schedule."""
    settings = build_settings()
    try:
        seeded = build_seeder(settings, build_celery(settings))(owner)
    except InvalidEmail:
        typer.echo("That isn't an email address.", err=True)
        raise typer.Exit(2) from None
    rivals = len(seeded.competitor_ids)
    typer.echo(f"Seeded Ola ({seeded.brand_id}) and {rivals} competitors.")
    typer.echo("The dispatcher queues their first scans within five minutes.")


@app.command("score-backlog")
def score_backlog() -> None:
    """Score the finished scans that have no scores yet (they raise no alerts)."""
    settings = build_settings()
    scored = build_scorer(settings, build_celery(settings))()
    typer.echo(f"Scored {scored} scans.")


@dataclass(frozen=True)
class Ran:
    report: str
    answered: int
    items: int
    cost_micros: int


@app.command("eval")
def run_eval(
    task: str = typer.Argument(..., help="The task to score: label_mentions or group_narratives."),
    split: str = typer.Option("test", help="Golden split: test, dev or all."),
    cap_cents: int = typer.Option(300, min=0, help="Stop before spending more, in US cents."),
    golden: Annotated[Path, typer.Option(help="Where the golden sets are.")] = GOLDEN,
    reports: Annotated[Path, typer.Option(help="Where the report goes.")] = REPORTS,
) -> None:
    """Score a prompt against its golden set with the real model (it spends credits); exits 1
    when some items went unanswered."""
    if task not in EVALS or split not in ("all", *Split):
        typer.echo(f"No eval for {task} on split {split}.", err=True)
        raise typer.Exit(2)
    lines = (golden / f"{task}.jsonl").read_text().splitlines()
    brand = (golden / f"{task}.brand.json").read_text()
    today = datetime.now(UTC).date().isoformat()
    try:
        ran = EVALS[LlmTask(task)](lines, brand, split, cap_cents * 10_000, today)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    path = reports / f"{today}-{task}.md"
    reports.mkdir(parents=True, exist_ok=True)
    path.write_text(ran.report)
    typer.echo(f"Scored {ran.answered} of {ran.items} items; report: {path}")
    typer.echo(f"Cost: ${ran.cost_micros / 1_000_000:.4f}")
    if ran.answered < ran.items:
        raise typer.Exit(1)


def _label_mentions(lines: list[str], brand: str, split: str, cap: int, today: str) -> Ran:
    items = [GoldenItem.model_validate_json(line) for line in lines if line.strip()]
    chosen = [item for item in items if split in ("all", item.split)]
    evaluator, model = build_evaluator(build_settings())
    result = evaluator.label_mentions(
        chosen, GoldenBrand.model_validate_json(brand), cap_micros=cap
    )
    text = report(result, on=today, model=model, split=split)
    return Ran(text, result.answered, result.items, result.cost_micros)


def _group_narratives(lines: list[str], brand: str, split: str, cap: int, today: str) -> Ran:
    items = [GoldenMention.model_validate_json(line) for line in lines if line.strip()]
    chosen = [item for item in items if split in ("all", item.split)]
    context = GroupingBrand.model_validate_json(brand)
    evaluator, model = build_grouping_evaluator(build_settings())
    result = evaluator.group_narratives(chosen, context, cap_micros=cap)
    text = grouping_report(result, context, on=today, model=model)
    return Ran(text, result.answered, len(chosen), result.cost_micros)


EVALS: Mapping[LlmTask, Callable[[list[str], str, str, int, str], Ran]] = MappingProxyType(
    {LlmTask.LABEL_MENTIONS: _label_mentions, LlmTask.GROUP_NARRATIVES: _group_narratives}
)
