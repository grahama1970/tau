"""Project trust storage for Tau coding resources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tau_coding.paths import TauPaths
from tau_coding.resources import TauResourcePaths

ProjectTrustDecision = bool | None
DefaultProjectTrust = Literal["ask", "always", "never"]

TRUST_REQUIRING_PROJECT_RESOURCES = (
    "settings.json",
    "skills",
    "prompts",
    "themes",
    "SYSTEM.md",
    "APPEND_SYSTEM.md",
    "AGENTS.md",
)


@dataclass(frozen=True, slots=True)
class ProjectTrustStoreEntry:
    """A saved or inherited project trust decision."""

    path: Path
    decision: bool


@dataclass(frozen=True, slots=True)
class ProjectTrustUpdate:
    """One project trust write."""

    path: Path
    decision: ProjectTrustDecision


@dataclass(frozen=True, slots=True)
class ProjectTrustOption:
    """One user-facing trust choice."""

    label: str
    trusted: bool
    updates: tuple[ProjectTrustUpdate, ...]
    saved_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ProjectTrustState:
    """Current trust state and available choices for a project."""

    cwd: Path
    saved_decision: ProjectTrustStoreEntry | None
    options: tuple[ProjectTrustOption, ...]


class ProjectTrustStore:
    """Read and write Tau project trust decisions."""

    def __init__(self, trust_path: Path) -> None:
        self.trust_path = trust_path

    @classmethod
    def from_resource_paths(cls, resource_paths: TauResourcePaths) -> ProjectTrustStore:
        paths = resource_paths.paths or TauPaths(
            home=resource_paths.root,
            agents_home=resource_paths.agents_root or Path.home() / ".agents",
        )
        return cls(paths.home / "trust.json")

    def get(self, cwd: Path) -> ProjectTrustDecision:
        entry = self.get_entry(cwd)
        return entry.decision if entry is not None else None

    def get_entry(self, cwd: Path) -> ProjectTrustStoreEntry | None:
        data = self._read()
        current = _normalize_path(cwd)
        while True:
            value = data.get(str(current))
            if value is True or value is False:
                return ProjectTrustStoreEntry(path=current, decision=value)
            if current.parent == current:
                return None
            current = current.parent

    def set(self, cwd: Path, decision: ProjectTrustDecision) -> None:
        self.set_many((ProjectTrustUpdate(path=cwd, decision=decision),))

    def set_many(self, updates: tuple[ProjectTrustUpdate, ...]) -> None:
        data = self._read()
        for update in updates:
            key = str(_normalize_path(update.path))
            if update.decision is None:
                data.pop(key, None)
            else:
                data[key] = update.decision
        self._write(data)

    def _read(self) -> dict[str, ProjectTrustDecision]:
        if not self.trust_path.exists():
            return {}
        try:
            parsed = json.loads(self.trust_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to read trust store {self.trust_path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"Invalid trust store {self.trust_path}: expected an object")

        data: dict[str, ProjectTrustDecision] = {}
        for key, value in parsed.items():
            if value is not True and value is not False and value is not None:
                raise ValueError(
                    f"Invalid trust store {self.trust_path}: value for {key!r} "
                    "must be true, false, or null"
                )
            data[str(key)] = value
        return data

    def _write(self, data: dict[str, ProjectTrustDecision]) -> None:
        serializable = {
            key: data[key]
            for key in sorted(data)
            if data[key] is True or data[key] is False or data[key] is None
        }
        self.trust_path.parent.mkdir(parents=True, exist_ok=True)
        self.trust_path.write_text(json.dumps(serializable, indent=2) + "\n", encoding="utf-8")


def project_trust_state(cwd: Path, store: ProjectTrustStore) -> ProjectTrustState:
    """Return saved decision and available choices for a project cwd."""
    resolved = _normalize_path(cwd)
    return ProjectTrustState(
        cwd=resolved,
        saved_decision=store.get_entry(resolved),
        options=get_project_trust_options(resolved),
    )


def get_project_trust_options(cwd: Path) -> tuple[ProjectTrustOption, ...]:
    """Return Pi-style durable trust choices for a project cwd."""
    trust_path = _normalize_path(cwd)
    options = [
        ProjectTrustOption(
            label="Trust",
            trusted=True,
            updates=(ProjectTrustUpdate(path=trust_path, decision=True),),
            saved_path=trust_path,
        )
    ]
    if trust_path.parent != trust_path:
        parent = trust_path.parent
        options.append(
            ProjectTrustOption(
                label=f"Trust parent folder ({parent})",
                trusted=True,
                updates=(
                    ProjectTrustUpdate(path=parent, decision=True),
                    ProjectTrustUpdate(path=trust_path, decision=None),
                ),
                saved_path=parent,
            )
        )
    options.append(
        ProjectTrustOption(
            label="Do not trust",
            trusted=False,
            updates=(ProjectTrustUpdate(path=trust_path, decision=False),),
            saved_path=trust_path,
        )
    )
    return tuple(options)


def has_trust_requiring_project_resources(cwd: Path, paths: TauPaths | None = None) -> bool:
    """Return whether cwd contains project-local resources that should be trusted."""
    tau_paths = paths or TauPaths()
    resolved_cwd = _normalize_path(cwd)
    tau_dir = tau_paths.project_tau_dir(resolved_cwd)
    if any((tau_dir / entry).exists() for entry in TRUST_REQUIRING_PROJECT_RESOURCES):
        return True

    home_agents_skills = _normalize_path(tau_paths.user_agents_skills_dir)
    current = resolved_cwd
    while True:
        agents_skills = _normalize_path(tau_paths.project_agents_skills_dir(current))
        if agents_skills != home_agents_skills and agents_skills.exists():
            return True
        if current.parent == current:
            return False
        current = current.parent


def _normalize_path(path: Path) -> Path:
    return path.expanduser().resolve()
