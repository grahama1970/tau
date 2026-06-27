"""Human-only goal-change validation for Tau."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TAU_HUMAN_GOAL_CHANGE_SCHEMA = "tau.human_goal_change.v1"


@dataclass(frozen=True, slots=True)
class HumanGoalChangeValidationResult:
    """Validation result for one human goal-change request."""

    ok: bool
    next_agent: str | None = None
    errors: tuple[str, ...] = ()


def validate_human_goal_change_file(
    path: Path,
    *,
    active_goal_hash: str | None = None,
    trusted_human: bool = False,
) -> HumanGoalChangeValidationResult:
    """Validate one human goal-change JSON file."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("human goal change root must be a JSON object")
    except Exception as exc:
        return HumanGoalChangeValidationResult(ok=False, errors=(f"invalid json: {exc}",))
    return validate_human_goal_change(
        payload,
        active_goal_hash=active_goal_hash,
        trusted_human=trusted_human,
    )


def validate_human_goal_change(
    payload: Mapping[str, Any],
    *,
    active_goal_hash: str | None = None,
    trusted_human: bool = False,
) -> HumanGoalChangeValidationResult:
    """Validate that a goal mutation is explicit and human-authorized."""

    errors: list[str] = []
    _require_fields(
        payload,
        (
            "schema",
            "github",
            "goal",
            "previous_subagent",
            "context",
            "new_goal",
            "rationale",
            "next_agent",
            "required_evidence",
            "stop_condition",
        ),
        "human_goal_change",
        errors,
    )
    if payload.get("schema") != TAU_HUMAN_GOAL_CHANGE_SCHEMA:
        errors.append(
            f"human_goal_change.schema must be {TAU_HUMAN_GOAL_CHANGE_SCHEMA!r}; "
            f"got {payload.get('schema')!r}"
        )
    if not trusted_human:
        errors.append("human goal change requires trusted human author")

    goal = _mapping_field(payload, "goal", "human_goal_change", errors)
    new_goal = _mapping_field(payload, "new_goal", "human_goal_change", errors)
    next_agent = _mapping_field(payload, "next_agent", "human_goal_change", errors)

    goal_hash = _non_empty_string(goal, "goal_hash", "goal", errors)
    if active_goal_hash and goal_hash and goal_hash != active_goal_hash:
        errors.append("human goal change must reference the current goal hash")
    previous_agent = _non_empty_string(payload, "previous_subagent", "human_goal_change", errors)
    if previous_agent != "human":
        errors.append("human goal change requires previous_subagent=human")

    _non_empty_string(new_goal, "text", "new_goal", errors)
    for field in ("success_criteria", "constraints", "non_goals"):
        if not isinstance(new_goal.get(field), list):
            errors.append(f"new_goal.{field} must be a list")

    next_name = _non_empty_string(next_agent, "name", "next_agent", errors)
    if next_name != "goal-guardian":
        errors.append("human goal change must route next_agent.name to goal-guardian")

    _non_empty_string(payload, "rationale", "human_goal_change", errors)
    if not isinstance(payload.get("required_evidence"), list):
        errors.append("human_goal_change.required_evidence must be a list")
    _non_empty_string(payload, "stop_condition", "human_goal_change", errors)

    return HumanGoalChangeValidationResult(
        ok=not errors,
        next_agent=next_name if not errors else None,
        errors=tuple(errors),
    )


def _mapping_field(
    payload: Mapping[str, Any],
    field: str,
    label: str,
    errors: list[str],
) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        errors.append(f"{label}.{field} must be an object")
        return {}
    return value


def _require_fields(
    payload: Mapping[str, Any],
    fields: tuple[str, ...],
    label: str,
    errors: list[str],
) -> None:
    for field in fields:
        if field not in payload:
            errors.append(f"{label}.{field} is required")


def _non_empty_string(
    payload: Mapping[str, Any],
    field: str,
    label: str,
    errors: list[str],
) -> str | None:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}.{field} must be a non-empty string")
        return None
    return value
