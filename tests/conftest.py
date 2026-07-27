"""Shared pytest fixtures that pin environment-dependent CLI rendering.

Many CLI tests assert that a specific message appears in ``result.output`` from
``typer.testing.CliRunner``. Typer renders those messages inside Rich panels,
and Rich emits ANSI style sequences whenever colour is *forced*, regardless of
whether the destination is a TTY. GitHub Actions runners export ``FORCE_COLOR``,
so the panel text arrives interleaved with escape sequences and every plain
substring assertion fails even though the CLI behaved correctly.

``NO_COLOR`` does not undo this: Rich gives ``FORCE_COLOR`` precedence. The only
reliable fix is to remove the forcing variables for the duration of the suite.
"""

import pytest

# Rich/Click honour these to force colour even when stdout is not a terminal.
_COLOUR_FORCING_ENV_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE")


@pytest.fixture(autouse=True)
def deterministic_cli_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render CLI output without ANSI styling, on developer machines and in CI.

    Applied to every test so that a runner which forces colour cannot change
    what the suite observes. Subprocess-based CLI probes inherit the cleaned
    environment because ``monkeypatch`` edits ``os.environ`` in place.
    """
    for var in _COLOUR_FORCING_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
