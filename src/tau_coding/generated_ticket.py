"""Minimal generated-ticket validation and GitHub projection for Tau."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TAU_AGENT_COMMON_SCHEMA = "tau.agent_common.v1"
TAU_AGENT_HANDOFF_SCHEMA = "tau.agent_handoff.v1"
TAU_GENERATED_TICKET_SCHEMA = "tau.generated_ticket.v1"

ROUTABLE_AGENTS = frozenset(
    {
        "human",
        "goal-guardian",
        "webgpt-ticket-author",
        "coder",
        "reviewer",
        "releaser",
    }
)
TICKET_CREATORS = frozenset({"chatgpt-pro", "chatgpt_pro", "webgpt", "webgpt-ticket-author"})
EXECUTORS = frozenset({"github-actions", "local", "either", "human"})
TICKET_KINDS = frozenset({"issue", "pull_request"})

REQUIRED_GENERATED_TICKET_FIELDS = (
    "schema",
    "github",
    "goal",
    "previous_subagent",
    "context",
    "ticket",
    "requested_work",
    "rationale",
    "next_agent",
    "required_evidence",
    "stop_condition",
    "goal_amendment_proposal",
)
REQUIRED_HANDOFF_FIELDS = (
    "schema",
    "github",
    "goal",
    "previous_subagent",
    "context",
    "result",
    "rationale",
    "next_agent",
    "required_evidence",
    "stop_condition",
)
REQUIRED_GOAL_FIELDS = ("goal_id", "goal_version", "goal_hash")
REQUIRED_CONTEXT_FIELDS = ("summary", "artifacts")
REQUIRED_TICKET_FIELDS = ("kind", "title", "body")
REQUIRED_NEXT_FIELDS = ("name", "reason")


@dataclass(frozen=True, slots=True)
class GithubProjection:
    """Deterministic GitHub issue projection derived by Tau."""

    kind: str
    title: str
    body: str
    labels: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable projection."""

        return {
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "labels": list(self.labels),
        }


@dataclass(frozen=True, slots=True)
class GeneratedTicketValidationResult:
    """Validation result for one minimal generated-ticket draft."""

    ok: bool
    next_agent: str | None = None
    github_create: dict[str, Any] | None = None
    errors: tuple[str, ...] = ()


def derived_labels(next_agent: str, executor: str | None = None) -> tuple[str, ...]:
    """Return the canonical labels Tau derives from a next-agent route."""

    resolved_executor = executor or "either"
    return ("agent-work", f"next:{next_agent}", f"executor:{resolved_executor}")


def load_generated_ticket(path: Path) -> dict[str, Any]:
    """Load one generated-ticket JSON file."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("generated ticket root must be a JSON object")
    return payload


def validate_generated_ticket_file(
    path: Path,
    *,
    active_goal_hash: str | None = None,
) -> GeneratedTicketValidationResult:
    """Validate one generated-ticket JSON file."""

    try:
        payload = load_generated_ticket(path)
    except Exception as exc:
        return GeneratedTicketValidationResult(ok=False, errors=(f"invalid json: {exc}",))
    return validate_generated_ticket(payload, active_goal_hash=active_goal_hash)


def validate_generated_ticket(
    payload: Mapping[str, Any],
    *,
    active_goal_hash: str | None = None,
) -> GeneratedTicketValidationResult:
    """Validate a minimal ChatGPT Pro/WebGPT ticket draft.

    The agent supplies ticket text and the next route. Tau derives labels and
    the GitHub create projection after validation.
    """

    errors: list[str] = []
    _require_fields(payload, REQUIRED_GENERATED_TICKET_FIELDS, "generated_ticket", errors)
    if payload.get("schema") != TAU_GENERATED_TICKET_SCHEMA:
        errors.append(
            f"generated_ticket.schema must be {TAU_GENERATED_TICKET_SCHEMA!r}; "
            f"got {payload.get('schema')!r}"
        )

    github = _mapping_field(payload, "github", "generated_ticket", errors)
    goal = _mapping_field(payload, "goal", "generated_ticket", errors)
    context = _mapping_field(payload, "context", "generated_ticket", errors)
    ticket = _mapping_field(payload, "ticket", "generated_ticket", errors)
    next_agent_payload = _mapping_field(payload, "next_agent", "generated_ticket", errors)

    _require_fields(github, ("repo",), "github", errors)
    _require_fields(goal, REQUIRED_GOAL_FIELDS, "goal", errors)
    _require_fields(context, REQUIRED_CONTEXT_FIELDS, "context", errors)
    _require_fields(ticket, REQUIRED_TICKET_FIELDS, "ticket", errors)
    _require_fields(next_agent_payload, REQUIRED_NEXT_FIELDS, "next_agent", errors)

    _non_empty_string(github, "repo", "github", errors)
    previous_agent = _non_empty_string(payload, "previous_subagent", "generated_ticket", errors)
    if previous_agent and previous_agent not in TICKET_CREATORS:
        errors.append(f"previous_subagent may not create tickets: {previous_agent}")

    goal_hash = _non_empty_string(goal, "goal_hash", "goal", errors)
    _non_empty_string(goal, "goal_id", "goal", errors)
    if not isinstance(goal.get("goal_version"), int):
        errors.append("goal.goal_version must be an integer")
    if active_goal_hash and goal_hash and goal_hash != active_goal_hash:
        errors.append("generated ticket may not change goal.goal_hash")

    _non_empty_string(context, "summary", "context", errors)
    if not isinstance(context.get("artifacts"), list):
        errors.append("context.artifacts must be a list")

    ticket_kind = _non_empty_string(ticket, "kind", "ticket", errors)
    ticket_title = _non_empty_string(ticket, "title", "ticket", errors)
    ticket_body = _non_empty_string(ticket, "body", "ticket", errors)
    if ticket_kind and ticket_kind not in TICKET_KINDS:
        errors.append(f"ticket.kind must be one of {sorted(TICKET_KINDS)}")

    next_name = _non_empty_string(next_agent_payload, "name", "next_agent", errors)
    next_executor = _optional_string(next_agent_payload, "executor", "next_agent", errors)
    _non_empty_string(next_agent_payload, "reason", "next_agent", errors)
    if next_name and next_name not in ROUTABLE_AGENTS:
        errors.append(f"next_agent.name must be one of {sorted(ROUTABLE_AGENTS)}")
    if next_executor and next_executor not in EXECUTORS:
        errors.append(f"next_agent.executor must be one of {sorted(EXECUTORS)}")

    _non_empty_string(payload, "requested_work", "generated_ticket", errors)
    _non_empty_string(payload, "rationale", "generated_ticket", errors)
    _require_string_list(payload, "required_evidence", "generated_ticket", errors)
    _non_empty_string(payload, "stop_condition", "generated_ticket", errors)

    goal_amendment = payload.get("goal_amendment_proposal")
    if goal_amendment is not None and next_name not in {"human", "goal-guardian"}:
        errors.append("goal_amendment_proposal requires next_agent.name human or goal-guardian")

    projection: GithubProjection | None = None
    if not errors and ticket_kind and ticket_title and ticket_body and next_name:
        projection = GithubProjection(
            kind=ticket_kind,
            title=ticket_title,
            body=ticket_body,
            labels=derived_labels(next_name, next_executor),
        )

    return GeneratedTicketValidationResult(
        ok=not errors,
        next_agent=next_name if not errors else None,
        github_create=projection.as_dict() if projection else None,
        errors=tuple(errors),
    )


def validate_agent_handoff(
    payload: Mapping[str, Any],
    *,
    active_goal_hash: str | None = None,
) -> GeneratedTicketValidationResult:
    """Validate a minimal handoff used by bounded subagents and comments."""

    errors: list[str] = []
    _require_fields(payload, REQUIRED_HANDOFF_FIELDS, "agent_handoff", errors)
    if payload.get("schema") != TAU_AGENT_HANDOFF_SCHEMA:
        errors.append(
            f"agent_handoff.schema must be {TAU_AGENT_HANDOFF_SCHEMA!r}; "
            f"got {payload.get('schema')!r}"
        )
    goal = _mapping_field(payload, "goal", "agent_handoff", errors)
    context = _mapping_field(payload, "context", "agent_handoff", errors)
    result = _mapping_field(payload, "result", "agent_handoff", errors)
    next_agent_payload = _mapping_field(payload, "next_agent", "agent_handoff", errors)
    _require_fields(goal, REQUIRED_GOAL_FIELDS, "goal", errors)
    _require_fields(context, REQUIRED_CONTEXT_FIELDS, "context", errors)
    _require_fields(result, ("status", "summary", "evidence"), "result", errors)
    _require_fields(next_agent_payload, REQUIRED_NEXT_FIELDS, "next_agent", errors)

    goal_hash = _non_empty_string(goal, "goal_hash", "goal", errors)
    if active_goal_hash and goal_hash and goal_hash != active_goal_hash:
        errors.append("agent handoff may not change goal.goal_hash")
    previous_agent = _non_empty_string(payload, "previous_subagent", "agent_handoff", errors)
    if previous_agent and previous_agent not in ROUTABLE_AGENTS and previous_agent != "chatgpt-pro":
        errors.append(f"previous_subagent is not recognized: {previous_agent}")
    next_name = _non_empty_string(next_agent_payload, "name", "next_agent", errors)
    next_executor = _optional_string(next_agent_payload, "executor", "next_agent", errors)
    if next_name and next_name not in ROUTABLE_AGENTS:
        errors.append(f"next_agent.name must be one of {sorted(ROUTABLE_AGENTS)}")
    if next_executor and next_executor not in EXECUTORS:
        errors.append(f"next_agent.executor must be one of {sorted(EXECUTORS)}")
    _non_empty_string(payload, "rationale", "agent_handoff", errors)
    _require_string_list(payload, "required_evidence", "agent_handoff", errors)
    _non_empty_string(payload, "stop_condition", "agent_handoff", errors)
    return GeneratedTicketValidationResult(
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


def _optional_string(
    payload: Mapping[str, Any],
    field: str,
    label: str,
    errors: list[str],
) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}.{field} must be a non-empty string when present")
        return None
    return value


def _require_string_list(
    payload: Mapping[str, Any],
    field: str,
    label: str,
    errors: list[str],
) -> list[str]:
    value = payload.get(field)
    if not isinstance(value, list):
        errors.append(f"{label}.{field} must be a list")
        return []
    strings = [item for item in value if isinstance(item, str) and item.strip()]
    if len(strings) != len(value):
        errors.append(f"{label}.{field} must contain only non-empty strings")
    return strings
