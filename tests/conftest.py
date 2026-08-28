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

import os
from pathlib import Path

import pytest

# Rich/Click honour these to force colour even when stdout is not a terminal.
_COLOUR_FORCING_ENV_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE")

_CONTRACT_TEST_FILES = frozenset(
    {
        "tests/test_agent_harness.py",
        "tests/test_agent_loop.py",
        "tests/test_dag_viewer_server.py",
        "tests/test_loop_receipt.py",
        "tests/test_loop_sanity.py",
        "tests/test_project_dag.py",
        "tests/test_prompt_templates.py",
        "tests/test_resources.py",
        "tests/test_run_ledger.py",
        "tests/test_sandbox_policy.py",
        "tests/test_secure_executor.py",
        "tests/test_skills.py",
        "tests/test_subagent_receipt.py",
        "tests/test_ticket_closure_evidence.py",
    }
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--tau-suite",
        choices=("contract", "all"),
        default=os.environ.get("TAU_PYTEST_SUITE", "contract"),
        help="Tau pytest diet: contract suite by default; use all for legacy unit wall.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Run the curated contract suite unless the legacy wall is explicit.

    The full historical pytest collection remains available with
    ``--tau-suite=all`` or ``TAU_PYTEST_SUITE=all``. The default path is a small
    deterministic regression suite; Tau capability proof must come from
    ``$agentic-evals``.
    """

    if config.getoption("--tau-suite") == "all":
        return

    root = Path(str(config.rootpath)).resolve()
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        path = Path(str(item.fspath)).resolve()
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        if rel in _CONTRACT_TEST_FILES:
            kept.append(item)
        else:
            deselected.append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


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
