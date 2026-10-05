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
    build_output_evaluator,
    build_scorer,
    build_seeder,
    build_settings,
)
from serpsense.composition_replay import STORY_BRANDS, build_recording_export, build_replayer
from serpsense.config import ConfigError, RunMode, Settings
from serpsense.domain.enums import LlmTask
from serpsense.ports.accounts import InvalidEmail
from serpsense.services.demo import DEMO_BRANDS
from serpsense.services.drafts import Outcome
from serpsense.services.evals import GoldenBrand, GoldenItem, Split, report
from serpsense.services.grouping_eval import GoldenMention, GroupingBrand, grouping_report
from serpsense.services.output_evals import DraftCase, ExplainCase, OutputResult
from serpsense.services.output_report import output_report
from serpsense.services.replay import Played

GOLDEN, REPORTS = Path("evals/golden"), Path("evals/reports")
RECORDINGS = Path("recordings")  # where `replay export` writes, from where it runs

app = typer.Typer(help="SerpSense command-line tools.", no_args_is_help=True)
replay = typer.Typer(help="Replay mode: recorded scans, played back with no API keys.")
app.add_typer(replay, name="replay", no_args_is_help=True)


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
    """Set up the Ola demo (BUILD_PLAN §22): Ola and four competitors on a schedule. In replay
    mode, the demo story instead: VoltBox and SoundNest, two weeks of scans played in, and
    scanned on request only."""
    settings = build_settings()
    if settings.serpsense_mode is RunMode.REPLAY:
        _replay(settings, owner)
        return
    try:
        seeded = build_seeder(settings, build_celery(settings))(owner)
    except InvalidEmail:
        typer.echo("That isn't an email address.", err=True)
        raise typer.Exit(2) from None
    rivals = len(seeded.competitor_ids)
    typer.echo(f"Seeded Ola ({seeded.brand_id}) and {rivals} competitors.")
    typer.echo("The dispatcher queues their first scans within five minutes.")


@replay.command("load")
def replay_load(
    owner: str = typer.Option(..., help="The demo owner's email address; created if new."),
) -> None:
    """Seed the demo story and play every scan of it, in order (SERPSENSE_MODE=replay)."""
    settings = build_settings()
    if settings.serpsense_mode is not RunMode.REPLAY:
        typer.echo("Recordings are played only with SERPSENSE_MODE=replay.", err=True)
        raise typer.Exit(2)
    _replay(settings, owner)


def _replay(settings: Settings, owner: str) -> None:
    try:
        replayed = build_replayer(settings, build_celery(settings))(owner)
    except InvalidEmail:
        typer.echo("That isn't an email address.", err=True)
        raise typer.Exit(2) from None
    played = replayed.played
    main, *rivals = STORY_BRANDS
    names = ", ".join(rival.name for rival in rivals)
    typer.echo(f"Seeded the demo story: {main.name} ({replayed.seeded.brand_id}) and {names}.")
    typer.echo(
        f"Played {played[Played.PLAYED]} recorded scans"
        f" ({played[Played.ALREADY]} played before, {played[Played.BUSY]} left for later);"
        " brands are scanned on request only."
    )
    if replayed.drafted is Outcome.DRAFTED:
        typer.echo("Drafted a holding statement for its largest story.")
    elif replayed.drafted is not None:
        typer.echo(
            f"Couldn't draft a holding statement ({replayed.drafted}); run it again to retry.",
            err=True,
        )


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
    task: str = typer.Argument(..., help="label_mentions, group_narratives, explain_crisis, …"),
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
    sidecar = golden / f"{task}.brand.json"  # a set that describes one brand has one
    brand = sidecar.read_text() if sidecar.exists() else ""
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


def _explain_crisis(lines: list[str], brand: str, split: str, cap: int, today: str) -> Ran:
    cases = [ExplainCase.model_validate_json(line) for line in lines if line.strip()]
    evaluator, model = build_output_evaluator(build_settings())
    chosen = [case for case in cases if split in ("all", case.split)]
    return _ran(evaluator.explanations(chosen, cap_micros=cap), today, model, split)


def _draft_response(lines: list[str], brand: str, split: str, cap: int, today: str) -> Ran:
    cases = [DraftCase.model_validate_json(line) for line in lines if line.strip()]
    evaluator, model = build_output_evaluator(build_settings())
    chosen = [case for case in cases if split in ("all", case.split)]
    return _ran(evaluator.drafts(chosen, cap_micros=cap), today, model, split)


def _ran(result: OutputResult, today: str, model: str, split: str) -> Ran:
    text = output_report(result, on=today, model=model, split=split)
    return Ran(text, result.passed["answered"], result.items, result.cost_micros)


EVALS: Mapping[LlmTask, Callable[[list[str], str, str, int, str], Ran]] = MappingProxyType(
    {
        LlmTask.LABEL_MENTIONS: _label_mentions,
        LlmTask.GROUP_NARRATIVES: _group_narratives,
        LlmTask.EXPLAIN_CRISIS: _explain_crisis,
        LlmTask.DRAFT_RESPONSE: _draft_response,
    }
)


@replay.command("export")
def replay_export(
    brand: Annotated[
        list[str] | None, typer.Option(help="A brand's slug; repeat it. Default: the demo's.")
    ] = None,
    out: Annotated[Path, typer.Option(help="Where the recordings go.")] = RECORDINGS,
) -> None:
    """Record brands' stored scans as replay recordings, one file each, to look at a brand's
    real answers or build a story from them. Read-only on the database. Replay mode itself
    plays the built-in demo story (adapters/replay/story)."""
    exporter = build_recording_export(build_settings())  # outside the try: never echo settings
    slugs = brand or [demo.slug for demo in DEMO_BRANDS]
    out.mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        try:
            exported = exporter.export(slug)
        except (LookupError, ValueError) as exc:  # no brand, or an unsafe recording refused
            typer.echo(f"{slug}: {exc}", err=True)
            raise typer.Exit(2) from exc
        (out / f"{slug}.json").write_text(exported.text, encoding="utf-8")
        typer.echo(
            f"{slug}: {exported.scans} scans ({exported.answers} answers),"
            f" {exported.labels} labels, {exported.narratives} stories."
        )
