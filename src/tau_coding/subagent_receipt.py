"""Goal-locked subagent receipt validation for Tau orchestration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TAU_SUBAGENT_RECEIPT_SCHEMA = "tau.subagent_receipt.v1"

REQUIRED_TOP_LEVEL_FIELDS = (
    "schema",
    "goal",
    "context",
    "result",
    "rationale",
    "evidence",
    "next",
    "stop_condition",
)
REQUIRED_GOAL_FIELDS = (
    "goal_id",
    "goal_version",
    "goal_hash",
    "immutable_goal_preserved",
)
REQUIRED_CONTEXT_FIELDS = ("run_id", "subagent", "actor_type")
REQUIRED_RESULT_FIELDS = ("status", "summary", "mocked", "live")
REQUIRED_NEXT_FIELDS = ("subagent", "reason", "executor")

HUMAN_ACTOR_TYPES = frozenset({"human"})


@dataclass(frozen=True, slots=True)
class SubagentReceiptValidationResult:
    """Validation result for one Tau subagent receipt."""

    ok: bool
    next_subagent: str | None = None
    errors: tuple[str, ...] = ()


def load_subagent_receipt(path: Path) -> dict[str, Any]:
    """Load one subagent receipt JSON file."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("receipt root must be a JSON object")
    return payload


def validate_subagent_receipt_file(
    path: Path,
    *,
    active_goal_hash: str | None = None,
) -> SubagentReceiptValidationResult:
    """Validate one subagent receipt JSON file."""

    try:
        payload = load_subagent_receipt(path)
    except Exception as exc:
        return SubagentReceiptValidationResult(ok=False, errors=(f"invalid json: {exc}",))
    return validate_subagent_receipt(payload, active_goal_hash=active_goal_hash)


def validate_subagent_receipt(
    payload: Mapping[str, Any],
    *,
    active_goal_hash: str | None = None,
) -> SubagentReceiptValidationResult:
    """Validate the common Tau receipt envelope required from every subagent.

    `active_goal_hash` is the immutable goal hash the outer harness currently
    trusts. A non-human receipt may not change it or mark the immutable goal as
    not preserved.
    """

    errors: list[str] = []
    _require_fields(payload, REQUIRED_TOP_LEVEL_FIELDS, "receipt", errors)

    if payload.get("schema") != TAU_SUBAGENT_RECEIPT_SCHEMA:
        errors.append(
            f"receipt.schema must be {TAU_SUBAGENT_RECEIPT_SCHEMA!r}; "
            f"got {payload.get('schema')!r}"
        )

    goal = _mapping_field(payload, "goal", errors)
    context = _mapping_field(payload, "context", errors)
    result = _mapping_field(payload, "result", errors)
    next_step = _mapping_field(payload, "next", errors)

    _require_fields(goal, REQUIRED_GOAL_FIELDS, "goal", errors)
    _require_fields(context, REQUIRED_CONTEXT_FIELDS, "context", errors)
    _require_fields(result, REQUIRED_RESULT_FIELDS, "result", errors)
    _require_fields(next_step, REQUIRED_NEXT_FIELDS, "next", errors)

    next_subagent = _non_empty_string(next_step, "subagent", "next", errors)
    _non_empty_string(next_step, "reason", "next", errors)
    _non_empty_string(next_step, "executor", "next", errors)
    _non_empty_string(context, "run_id", "context", errors)
    _non_empty_string(context, "subagent", "context", errors)
    actor_type = _non_empty_string(context, "actor_type", "context", errors)
    goal_hash = _non_empty_string(goal, "goal_hash", "goal", errors)
    _non_empty_string(goal, "goal_id", "goal", errors)
    _non_empty_string(payload, "rationale", "receipt", errors)
    _non_empty_string(payload, "stop_condition", "receipt", errors)

    if not isinstance(payload.get("evidence"), list):
        errors.append("receipt.evidence must be a list")

    if not isinstance(goal.get("goal_version"), int):
        errors.append("goal.goal_version must be an integer")
    if not isinstance(goal.get("immutable_goal_preserved"), bool):
        errors.append("goal.immutable_goal_preserved must be a boolean")

    is_human = actor_type in HUMAN_ACTOR_TYPES
    if active_goal_hash and goal_hash and goal_hash != active_goal_hash and not is_human:
        errors.append("non-human subagent may not change goal.goal_hash")
    if goal.get("immutable_goal_preserved") is False and not is_human:
        errors.append("non-human subagent may not set immutable_goal_preserved=false")

    return SubagentReceiptValidationResult(
        ok=not errors,
        next_subagent=next_subagent if not errors else None,
        errors=tuple(errors),
    )


def _mapping_field(
    payload: Mapping[str, Any],
    field: str,
    errors: list[str],
) -> Mapping[str, Any]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        errors.append(f"receipt.{field} must be an object")
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
