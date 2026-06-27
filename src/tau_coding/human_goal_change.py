"""Human-only goal-change validation and bridge for Tau."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_coding.generated_ticket import TAU_AGENT_HANDOFF_SCHEMA, project_agent_handoff

TAU_HUMAN_GOAL_CHANGE_SCHEMA = "tau.human_goal_change.v1"
TAU_HUMAN_GOAL_CHANGE_BRIDGE_RECEIPT_SCHEMA = "tau.human_goal_change_bridge_receipt.v1"
_GOAL_GUARDIAN_RECONCILIATION_EVIDENCE = (
    "goal-guardian posts a reconciliation receipt before any non-human agent continues"
)
_GOAL_GUARDIAN_STOP_TIGHTENING = (
    "Goal-guardian must reconcile the human-only goal-change request before any "
    "non-human agent continues."
)


@dataclass(frozen=True, slots=True)
class HumanGoalChangeValidationResult:
    """Validation result for one human goal-change request."""

    ok: bool
    next_agent: str | None = None
    errors: tuple[str, ...] = ()


def load_human_goal_change(path: Path) -> dict[str, Any]:
    """Load one human goal-change JSON object."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("human goal change root must be a JSON object")
    return payload


def validate_human_goal_change_file(
    path: Path,
    *,
    active_goal_hash: str | None = None,
    trusted_human: bool = False,
) -> HumanGoalChangeValidationResult:
    """Validate one human goal-change JSON file."""

    try:
        payload = load_human_goal_change(path)
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


def bridge_human_goal_change_to_handoff(
    payload: Mapping[str, Any],
    *,
    active_goal_hash: str | None,
    trusted_human: bool = False,
    source: str | None = None,
    agent_registry_root: Path | None = None,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Convert a trusted human goal-change packet into a normal Tau start handoff."""

    errors: list[str] = []
    if not isinstance(active_goal_hash, str) or not active_goal_hash.strip():
        errors.append("human goal change bridge requires active_goal_hash")

    validation = validate_human_goal_change(
        payload,
        active_goal_hash=active_goal_hash,
        trusted_human=trusted_human,
    )
    errors.extend(validation.errors)
    if errors:
        return None, tuple(errors)

    github = _mapping_field(payload, "github", "human_goal_change", errors)
    goal = _mapping_field(payload, "goal", "human_goal_change", errors)
    context = _mapping_field(payload, "context", "human_goal_change", errors)
    new_goal = _mapping_field(payload, "new_goal", "human_goal_change", errors)
    next_agent = _mapping_field(payload, "next_agent", "human_goal_change", errors)
    context_summary = _non_empty_string(context, "summary", "context", errors)
    next_reason = _non_empty_string(next_agent, "reason", "next_agent", errors)
    rationale = _non_empty_string(payload, "rationale", "human_goal_change", errors)
    stop_condition = _non_empty_string(payload, "stop_condition", "human_goal_change", errors)
    artifacts = _string_list(context.get("artifacts"), "context.artifacts", errors)
    required_evidence = _string_list(
        payload.get("required_evidence"),
        "human_goal_change.required_evidence",
        errors,
    )
    new_goal_copy = _json_safe_copy(new_goal, "new_goal", errors)
    if errors:
        return None, tuple(errors)

    packet_ref = source or f"in-memory {TAU_HUMAN_GOAL_CHANGE_SCHEMA} payload"
    bridged_artifacts = list(artifacts)
    if source and source not in bridged_artifacts:
        bridged_artifacts.append(source)

    tightened_required_evidence = list(required_evidence)
    if _GOAL_GUARDIAN_RECONCILIATION_EVIDENCE not in tightened_required_evidence:
        tightened_required_evidence.append(_GOAL_GUARDIAN_RECONCILIATION_EVIDENCE)

    tightened_stop_condition = str(stop_condition)
    if _GOAL_GUARDIAN_STOP_TIGHTENING not in tightened_stop_condition:
        tightened_stop_condition = f"{tightened_stop_condition} {_GOAL_GUARDIAN_STOP_TIGHTENING}"

    handoff: dict[str, Any] = {
        "schema": TAU_AGENT_HANDOFF_SCHEMA,
        "github": dict(github),
        "goal": dict(goal),
        "previous_subagent": "human",
        "context": {
            "summary": (
                "Trusted human requested immutable goal-change reconciliation. "
                f"Original summary: {context_summary}"
            ),
            "artifacts": bridged_artifacts,
            "human_goal_change": {
                "schema": TAU_HUMAN_GOAL_CHANGE_SCHEMA,
                "source": packet_ref,
                "new_goal": new_goal_copy,
                "rationale": rationale,
            },
        },
        "result": {
            "status": "GOAL_CHANGE_REQUESTED",
            "summary": "Trusted human requested goal-change reconciliation by goal-guardian.",
            "evidence": [f"validated {TAU_HUMAN_GOAL_CHANGE_SCHEMA} packet: {packet_ref}"],
        },
        "rationale": (
            "A trusted human requested an immutable goal change. Tau must route the "
            "proposal to goal-guardian before any non-human agent continues. "
            f"Human rationale: {rationale}"
        ),
        "next_agent": {
            "name": "goal-guardian",
            "executor": "local",
            "reason": next_reason,
        },
        "required_evidence": tightened_required_evidence,
        "stop_condition": tightened_stop_condition,
    }

    projection = project_agent_handoff(
        handoff,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    )
    if not projection.ok:
        return None, tuple(f"generated handoff invalid: {error}" for error in projection.errors)
    return handoff, ()


def build_human_goal_change_bridge_receipt(
    payload: Mapping[str, Any],
    *,
    active_goal_hash: str | None,
    trusted_human: bool,
    source: str | None = None,
    handoff_path: Path | None = None,
    agent_registry_root: Path | None = None,
) -> dict[str, Any]:
    """Build a machine-readable receipt for the local bridge transformation."""

    handoff, errors = bridge_human_goal_change_to_handoff(
        payload,
        active_goal_hash=active_goal_hash,
        trusted_human=trusted_human,
        source=source,
        agent_registry_root=agent_registry_root,
    )
    output_schema = TAU_AGENT_HANDOFF_SCHEMA if handoff is not None else None
    next_agent = None
    if handoff is not None and isinstance(handoff.get("next_agent"), Mapping):
        next_agent = handoff["next_agent"].get("name")

    return {
        "schema": TAU_HUMAN_GOAL_CHANGE_BRIDGE_RECEIPT_SCHEMA,
        "ok": handoff is not None and not errors,
        "dry_run": True,
        "trusted_human": trusted_human,
        "source": source,
        "active_goal_hash": active_goal_hash,
        "input_schema": payload.get("schema"),
        "output_schema": output_schema,
        "next_agent": next_agent,
        "handoff_path": str(handoff_path.expanduser().resolve()) if handoff_path else None,
        "handoff_sha256": _canonical_json_sha256(handoff) if handoff is not None else None,
        "start_handoff": handoff,
        "errors": list(errors),
    }


def write_human_goal_change_bridge_receipt(
    payload: Mapping[str, Any],
    receipt_path: Path,
    *,
    handoff_path: Path,
    active_goal_hash: str | None,
    trusted_human: bool,
    source: str | None = None,
    agent_registry_root: Path | None = None,
) -> dict[str, Any]:
    """Write the bridge receipt and, on success only, the generated start handoff."""

    receipt = build_human_goal_change_bridge_receipt(
        payload,
        active_goal_hash=active_goal_hash,
        trusted_human=trusted_human,
        source=source,
        handoff_path=handoff_path,
        agent_registry_root=agent_registry_root,
    )
    if receipt["ok"] is True and isinstance(receipt.get("start_handoff"), dict):
        _write_json(handoff_path, receipt["start_handoff"])
    _write_json(receipt_path, receipt)
    return receipt


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


def _string_list(value: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    strings: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{label}[{index}] must be a non-empty string")
        else:
            strings.append(item)
    return strings


def _json_safe_copy(value: Any, label: str, errors: list[str]) -> Any:
    try:
        return json.loads(json.dumps(value, sort_keys=True))
    except (TypeError, ValueError) as exc:
        errors.append(f"{label} must be JSON-serializable: {exc}")
        return None


def _canonical_json_sha256(value: Mapping[str, Any]) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
