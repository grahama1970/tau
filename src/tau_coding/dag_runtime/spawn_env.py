"""Deny-by-default subprocess environment for governed skill execution.

A skill node spawned with ``dict(os.environ)`` inherits every host secret. This
module inverts that: the child gets a small fixed base (enough for ``uv``,
``git``, and locale-correct tools) plus only the variable names the contract
explicitly declares in ``env_passthrough``. A declared-but-absent variable is
reported back to the caller so the receipt can name the missing declaration
instead of the tool failing mysteriously downstream.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass

# Enough for uv/python/git/shell tooling and locale correctness — nothing that
# carries credentials. Extend deliberately; never add wildcard families here.
BASE_SPAWN_ENV_VARS = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "USER",
    "SHELL",
)

ENV_PASSTHROUGH_FIELD = "env_passthrough"


@dataclass(frozen=True, slots=True)
class GovernedSpawnEnv:
    env: dict[str, str]
    missing_passthrough: tuple[str, ...]

    def diagnostic(self) -> str | None:
        """One actionable line for receipts when a declared variable is absent."""

        if not self.missing_passthrough:
            return None
        names = ", ".join(self.missing_passthrough)
        return (
            f"declared {ENV_PASSTHROUGH_FIELD} variables absent from the host "
            f"environment: {names}"
        )


def build_governed_spawn_env(
    passthrough: Iterable[str] = (),
    *,
    source: dict[str, str] | None = None,
) -> GovernedSpawnEnv:
    """Base allowlist + explicitly declared passthrough names, nothing else."""

    host = os.environ if source is None else source
    env: dict[str, str] = {}
    for name in BASE_SPAWN_ENV_VARS:
        value = host.get(name)
        if value is not None:
            env[name] = value
    missing: list[str] = []
    for name in passthrough:
        value = host.get(name)
        if value is None:
            missing.append(name)
        else:
            env[name] = value
    return GovernedSpawnEnv(env=env, missing_passthrough=tuple(missing))


__all__ = [
    "BASE_SPAWN_ENV_VARS",
    "ENV_PASSTHROUGH_FIELD",
    "GovernedSpawnEnv",
    "build_governed_spawn_env",
]
