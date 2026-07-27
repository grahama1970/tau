"""Shared pytest fixtures that pin environment-dependent CLI rendering.

Many CLI tests assert that a specific message appears in ``result.output`` from
``typer.testing.CliRunner``. Typer renders those messages inside Rich panels, and
Rich decides whether to emit ANSI sequences from the ambient environment, not
from whether the destination is a TTY. Two settings change what the suite sees:

* ``TERM`` unset — Rich treats the terminal as style-capable and emits dim/bold
  sequences (``\\x1b[2m``, ``\\x1b[1;2m``) inside the panel text. GitHub Actions
  runners do not set ``TERM``, which is what broke 35 assertions there while the
  same commit passed locally. ``TERM=dumb`` disables styling outright.
* ``FORCE_COLOR`` / ``CLICOLOR_FORCE`` — force colour regardless of destination.

``NO_COLOR`` alone is not sufficient: it suppresses colour but leaves the dim and
bold attributes in place, so the substring assertions still fail.
"""

import pytest

# Rich/Click honour these to force colour even when stdout is not a terminal.
_COLOUR_FORCING_ENV_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE")


@pytest.fixture(autouse=True)
def deterministic_cli_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render CLI output without ANSI styling, on developer machines and in CI.

    Applied to every test so that the runner's terminal settings cannot change
    what the suite observes. Subprocess-based CLI probes inherit the pinned
    environment because ``monkeypatch`` edits ``os.environ`` in place.
    """
    for var in _COLOUR_FORCING_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
