"""Command-line entry point for Tau."""

import typer

from tau_coding import __version__

app = typer.Typer(
    name="tau",
    help="Tau coding-agent harness.",
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show Tau's version and exit.",
    ),
) -> None:
    """Run the Tau CLI."""
    if version:
        typer.echo(f"tau {__version__}")
        raise typer.Exit()

    typer.echo("Tau phase 0 scaffold is installed. Run `tau --version` to verify the CLI.")
