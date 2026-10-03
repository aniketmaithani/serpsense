"""Command-line interface."""

import base64
import secrets

import typer

from serpsense import __version__
from serpsense.composition import build_celery, build_scorer, build_seeder, build_settings
from serpsense.ports.accounts import InvalidEmail

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
