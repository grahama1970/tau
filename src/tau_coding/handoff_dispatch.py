"""One-step handoff dispatch receipts for Tau agent orchestration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau_coding.generated_ticket import project_agent_handoff

TAU_AGENT_HANDOFF_DISPATCH_RECEIPT_SCHEMA = "tau.agent_handoff_dispatch_receipt.v1"


@dataclass(frozen=True, slots=True)
class AgentHandoffDispatchResult:
    """Receipt for one selected subagent response consumed from a file runner."""

    ok: bool
    status: str
    selected_agent: str | None = None
    stop_reason: str | None = None
    mocked: bool = True
    live: bool = False
    runner: str = "file-response"
    start_projection: dict[str, Any] | None = None
    response_projection: dict[str, Any] | None = None
    receipt_dir: str | None = None
    artifacts: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dispatch receipt."""

        return {
            "schema": TAU_AGENT_HANDOFF_DISPATCH_RECEIPT_SCHEMA,
            "ok": self.ok,
            "status": self.status,
            "selected_agent": self.selected_agent,
            "stop_reason": self.stop_reason,
            "mocked": self.mocked,
            "live": self.live,
            "runner": self.runner,
            "start_projection": self.start_projection,
            "response_projection": self.response_projection,
            "receipt_dir": self.receipt_dir,
            "artifacts": list(self.artifacts),
            "errors": list(self.errors),
        }


def dispatch_agent_handoff_once(
    start_payload: Mapping[str, Any],
    response_by_agent: Mapping[str, Mapping[str, Any]],
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> AgentHandoffDispatchResult:
    """Consume one handoff route and validate exactly one selected agent response."""

    start_projection = project_agent_handoff(
        start_payload,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    ).as_dict()
    if start_projection.get("ok") is not True:
        return AgentHandoffDispatchResult(
            ok=False,
            status="BLOCKED",
            selected_agent=start_projection.get("next_agent"),
            stop_reason="invalid_start_handoff",
            start_projection=start_projection,
            errors=tuple(f"start: {error}" for error in start_projection.get("errors", [])),
        )

    selected_agent = str(start_projection.get("next_agent") or "")
    if selected_agent == "human":
        return AgentHandoffDispatchResult(
            ok=True,
            status="WAITING",
            selected_agent=selected_agent,
            stop_reason="next_agent_is_human",
            start_projection=start_projection,
        )

    response_payload = response_by_agent.get(selected_agent)
    if response_payload is None:
        return AgentHandoffDispatchResult(
            ok=True,
            status="WAITING",
            selected_agent=selected_agent,
            stop_reason="missing_agent_response",
            start_projection=start_projection,
        )

    response_projection = project_agent_handoff(
        response_payload,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    ).as_dict()
    errors = _response_continuity_errors(
        start_payload=start_payload,
        start_projection=start_projection,
        response_payload=response_payload,
        response_projection=response_projection,
        selected_agent=selected_agent,
    )
    if response_projection.get("ok") is not True:
        errors.extend(f"response: {error}" for error in response_projection.get("errors", []))

    if errors:
        return AgentHandoffDispatchResult(
            ok=False,
            status="BLOCKED",
            selected_agent=selected_agent,
            stop_reason="invalid_agent_response",
            start_projection=start_projection,
            response_projection=response_projection,
            errors=tuple(errors),
        )

    return AgentHandoffDispatchResult(
        ok=True,
        status="COMPLETED",
        selected_agent=selected_agent,
        stop_reason="response_consumed",
        start_projection=start_projection,
        response_projection=response_projection,
    )


def write_agent_handoff_dispatch_receipt(
    start_payload: Mapping[str, Any],
    response_by_agent: Mapping[str, Mapping[str, Any]],
    receipt_dir: Path,
    *,
    active_goal_hash: str | None = None,
    agent_registry_root: Path | None = None,
) -> AgentHandoffDispatchResult:
    """Write one dispatch receipt plus per-step projection artifacts."""

    receipt_dir.mkdir(parents=True, exist_ok=True)
    dispatch = dispatch_agent_handoff_once(
        start_payload,
        response_by_agent,
        active_goal_hash=active_goal_hash,
        agent_registry_root=agent_registry_root,
    )
    artifacts: list[str] = []
    if dispatch.start_projection is not None:
        start_path = receipt_dir / "start-handoff.receipt.json"
        _write_json(start_path, dispatch.start_projection)
        artifacts.append(str(start_path))
    if dispatch.response_projection is not None:
        response_path = receipt_dir / f"{dispatch.selected_agent}-response.receipt.json"
        _write_json(response_path, dispatch.response_projection)
        artifacts.append(str(response_path))

    receipt_payload = {
        **dispatch.as_dict(),
        "receipt_dir": str(receipt_dir),
        "artifacts": artifacts,
    }
    _write_json(receipt_dir / "dispatch-receipt.json", receipt_payload)
    return AgentHandoffDispatchResult(
        ok=dispatch.ok,
        status=dispatch.status,
        selected_agent=dispatch.selected_agent,
        stop_reason=dispatch.stop_reason,
        start_projection=dispatch.start_projection,
        response_projection=dispatch.response_projection,
        receipt_dir=str(receipt_dir),
        artifacts=tuple(artifacts),
        errors=dispatch.errors,
    )


def _response_continuity_errors(
    *,
    start_payload: Mapping[str, Any],
    start_projection: Mapping[str, Any],
    response_payload: Mapping[str, Any],
    response_projection: Mapping[str, Any],
    selected_agent: str,
) -> list[str]:
    errors: list[str] = []
    if response_payload.get("previous_subagent") != selected_agent:
        errors.append(
            "response.previous_subagent must equal selected_agent "
            f"{selected_agent!r}; got {response_payload.get('previous_subagent')!r}"
        )
    if response_projection.get("target") != start_projection.get("target"):
        errors.append(
            "response.github target must match start handoff target "
            f"{start_projection.get('target')!r}; got {response_projection.get('target')!r}"
        )
    start_goal = _goal_key(start_payload.get("goal"))
    response_goal = _goal_key(response_payload.get("goal"))
    if response_goal != start_goal:
        errors.append(
            f"response.goal must match start handoff goal {start_goal!r}; "
            f"got {response_goal!r}"
        )
    return errors


def _goal_key(value: object) -> tuple[Any, Any, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return (value.get("goal_id"), value.get("goal_version"), value.get("goal_hash"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
