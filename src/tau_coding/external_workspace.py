"""Locate sibling repositories Tau shells out to.

Several modules invoke scripts and fixtures that live in adjacent checkouts —
agent-skills and scillm — rather than inside this repository. Those locations
used to be absolute paths belonging to one developer's machine, which shipped
inside the wheel and made the corresponding code unusable anywhere else.

Resolution order, first existing candidate wins:

1. An explicit environment variable, so an operator can say where the checkout is.
2. A sibling of this repository, which is how the checkouts are normally arranged.
3. ``~/workspace/experiments/<name>``, the previously hardcoded layout.

The final fallback is returned even when it does not exist, so callers still get
a concrete path to report in an error message instead of ``None``.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

AGENT_SKILLS_ENV_VAR = "TAU_AGENT_SKILLS_ROOT"
SCILLM_ENV_VAR = "TAU_SCILLM_ROOT"


def _resolve(name: str, env_var: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        return Path(override).expanduser()
    candidates = (
        _REPO_ROOT.parent / name,
        Path.home() / "workspace" / "experiments" / name,
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[-1]


def agent_skills_root() -> Path:
    """Root of the adjacent agent-skills checkout."""
    return _resolve("agent-skills", AGENT_SKILLS_ENV_VAR)


def scillm_root() -> Path:
    """Root of the adjacent scillm checkout."""
    return _resolve("scillm", SCILLM_ENV_VAR)
