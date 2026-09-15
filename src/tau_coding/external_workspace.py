"""Locate sibling repositories Tau shells out to.

Several modules invoke scripts and fixtures that live in adjacent checkouts —
agent-skills and scillm — rather than inside this repository. Resolution is
explicit: an environment variable wins, otherwise Tau checks for a sibling of
this source tree. Installed wheels must not assume one developer's checkout
layout.
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
    candidate = _REPO_ROOT.parent / name
    return candidate


def agent_skills_root() -> Path:
    """Root of the adjacent agent-skills checkout."""
    return _resolve("agent-skills", AGENT_SKILLS_ENV_VAR)


def scillm_root() -> Path:
    """Root of the adjacent scillm checkout."""
    return _resolve("scillm", SCILLM_ENV_VAR)
