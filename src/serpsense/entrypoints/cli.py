"""Command-line interface."""

import base64
import secrets

import typer

from serpsense import __version__

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
